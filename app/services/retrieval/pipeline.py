"""Retrieval processing pipeline.

Retrieval stages: fusion → diversity → rerank → expansion → budget packing.
Policy stages: sufficiency check → bounded retry → compare-policy audit →
answer synthesis (optional).

Pure orchestration around the search and evidence services. LLM calls
happen in answer synthesis and, when the rule-based rewrite finds no exact
phrase, in the dense/sparse branches' query reformulation. ``run()`` serves
callers who only need the ``EvidenceBundle``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.domain.models.retrieval import (
    EvidenceBundle,
    RetrievalMode,
    RetrievalQuery,
    RetrievedChunk,
)
from app.domain.models.retrieval.sufficiency import SufficiencyStatus, SufficiencyVerdict
from app.services.retrieval.answer.grounding import abstain_message, check_identifiers
from app.services.retrieval.answer.synthesis import (
    AnswerSynthesizer,
    PromptAnswerSynthesizer,
    SynthesisResult,
)
from app.services.retrieval.evidence.compare_policy import CompareResult, enforce_compare_diversity
from app.services.retrieval.evidence.diversity import apply_diversity
from app.services.retrieval.evidence.packing import build_bundle
from app.services.retrieval.evidence.retry import RetryPolicy, apply_retry_strategy
from app.services.retrieval.evidence.sufficiency import SufficiencyChecker
from app.services.retrieval.provider import RowProvider, SqlAlchemyRowProvider
from app.services.retrieval.search.expansion import ExpansionService, resolve_expansion_policy
from app.services.retrieval.search.rerank import IdentityReranker, Reranker

logger = logging.getLogger(__name__)

# Rerank input of ~20-50 children balances recall against reranker latency.
DEFAULT_RERANK_TOP_K_MAX = 48

_NOT_ENOUGH_INTERNAL = "Tôi không tìm thấy đủ tài liệu nội bộ để trả lời câu hỏi này."
_NOT_ENOUGH_EXTERNAL = "Không có tài liệu phù hợp để trả lời câu hỏi này."


@dataclass(frozen=True)
class _Gate:
    """Outcome of the pre-synthesis checks: a refusal, or the missing documents to warn of."""

    refusal: str | None = None
    missing_documents: list[str] | None = None


def _gate(
    query: RetrievalQuery,
    bundle: EvidenceBundle,
    verdict: SufficiencyVerdict,
    compare_result: CompareResult | None,
    *,
    internal_only: bool,
) -> _Gate:
    """Decide whether synthesis may run, shared by the plain and streaming paths.

    No evidence always refuses; for internal-only answers an INSUFFICIENT
    verdict or an identifier the evidence never mentions refuses too. A
    coverage warning only makes sense when the user compares specific
    documents: scoping RAG to a set of candidates (metadata route, document
    generation) leaves most of them uncited by design.
    """
    if not bundle.items:
        return _Gate(refusal=_NOT_ENOUGH_INTERNAL if internal_only else _NOT_ENOUGH_EXTERNAL)
    if verdict.status is SufficiencyStatus.INSUFFICIENT and internal_only:
        return _Gate(refusal=_NOT_ENOUGH_INTERNAL)
    identifiers = check_identifiers(query.original_query, bundle)
    if identifiers.abstain and internal_only:
        return _Gate(refusal=abstain_message(identifiers, bundle))
    missing: list[str] = (
        list(verdict.missing_document_ids) if query.mode is RetrievalMode.COMPARE_DOCUMENTS else []
    )
    if compare_result and compare_result.missing_documents:
        missing = sorted(set(missing + compare_result.missing_documents))
    return _Gate(missing_documents=missing or None)


@runtime_checkable
class HybridRetriever(Protocol):
    """Duck-typed seam any fused-candidate source must satisfy."""

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]: ...


class RetrievalPipeline:
    """Composes the full retrieval stage chain: deterministic retrieval, then policies."""

    def __init__(
        self,
        hybrid_service: HybridRetriever,
        provider: RowProvider | None = None,
        *,
        reranker: Reranker | None = None,
        per_document_cap: int | None = None,
        rerank_top_k_max: int = DEFAULT_RERANK_TOP_K_MAX,
        sufficiency_checker: SufficiencyChecker | None = None,
        retry_policy: RetryPolicy | None = None,
        synthesizer: AnswerSynthesizer | None = None,
    ) -> None:
        self._hybrid = hybrid_service
        self._reranker = reranker or IdentityReranker()
        self._expansion = ExpansionService(provider or SqlAlchemyRowProvider())
        self._per_document_cap = per_document_cap
        self._rerank_top_k_max = max(int(rerank_top_k_max), 1)
        self._sufficiency = sufficiency_checker or SufficiencyChecker()
        self._retry_policy = retry_policy or RetryPolicy()
        self._synthesizer: AnswerSynthesizer = synthesizer or PromptAnswerSynthesizer()

    async def run(self, query: RetrievalQuery) -> EvidenceBundle:
        """Fusion → diversity hygiene → rerank → expansion → packing."""
        trace_id = uuid.uuid4().hex
        bundle = await self._retrieve_and_pack(query, trace_id)
        return bundle

    async def run_with_sufficiency(
        self, query: RetrievalQuery
    ) -> tuple[EvidenceBundle, SufficiencyVerdict, CompareResult | None]:
        """Retrieval + sufficiency check + bounded retry.

        Returns ``(bundle, verdict, compare_result)`` after up to
        ``max_attempts`` retrieval rounds.
        """
        root_trace_id = uuid.uuid4().hex
        attempt = 1
        bundle = await self._retrieve_and_pack(query, f"{root_trace_id}-att1")

        verdict = self._sufficiency.check(bundle, query)
        current_query = query

        while verdict.status is not SufficiencyStatus.SUFFICIENT:
            retry_query = apply_retry_strategy(
                current_query, verdict, attempt, policy=self._retry_policy
            )
            if retry_query is None:
                break
            attempt += 1
            bundle = await self._retrieve_and_pack(retry_query, f"{root_trace_id}-att{attempt}")
            verdict = self._sufficiency.check(bundle, retry_query)
            current_query = retry_query

        compare_result: CompareResult | None = None
        if current_query.mode is RetrievalMode.COMPARE_DOCUMENTS and current_query.document_ids:
            compare_result = enforce_compare_diversity(bundle, current_query.document_ids)

        logger.info(
            "retrieval_pipeline_sufficiency_done",
            extra={
                "retrieval_trace_id": bundle.retrieval_trace_id,
                "sufficiency": verdict.status.value,
                "attempts": attempt,
                "items": len(bundle.items),
            },
        )
        return bundle, verdict, compare_result

    async def run_with_synthesis(
        self,
        query: RetrievalQuery,
        *,
        internal_only: bool = True,
        conversation_history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
        extra_images: str = "",
    ) -> SynthesisResult:
        """Full path: retrieval → sufficiency → retry → synthesis.

        ``extra_images``: image references the answer may show besides those of
        the evidence (a report section's source figures).

        Preserves PARTIAL signal and propagates missing document warnings
        to synthesis prompt.
        """
        bundle, verdict, compare_result = await self.run_with_sufficiency(query)
        gate = _gate(query, bundle, verdict, compare_result, internal_only=internal_only)
        if gate.refusal is not None:
            return SynthesisResult(
                answer=gate.refusal, status=SufficiencyStatus.INSUFFICIENT, citations=[]
            )

        return await self._synthesizer.synthesize(
            query.original_query,
            bundle,
            internal_only=internal_only,
            missing_documents=gate.missing_documents,
            verdict_status=verdict.status,
            conversation_history=conversation_history,
            **({"system_prompt": system_prompt} if system_prompt else {}),
            **({"extra_images": extra_images} if extra_images else {}),
        )

    async def run_with_streaming_synthesis(
        self,
        query: RetrievalQuery,
        *,
        internal_only: bool = True,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Full path with real-time token streaming: retrieval → sufficiency → streaming synthesis.

        Yields dicts with:
        - `{"type": "token", "delta": "..."}`
        - `{"type": "citations", "citations": [...], "status": "..."}`
        """
        bundle, verdict, compare_result = await self.run_with_sufficiency(query)
        gate = _gate(query, bundle, verdict, compare_result, internal_only=internal_only)
        if gate.refusal is not None:
            yield {"type": "token", "delta": gate.refusal}
            yield {
                "type": "citations",
                "citations": [],
                "status": SufficiencyStatus.INSUFFICIENT.value,
            }
            return

        async for item in self._synthesizer.synthesize_stream(
            query.original_query,
            bundle,
            internal_only=internal_only,
            missing_documents=gate.missing_documents,
            verdict_status=verdict.status,
            conversation_history=conversation_history,
        ):
            yield item

    async def _retrieve_and_pack(self, query: RetrievalQuery, trace_id: str) -> EvidenceBundle:
        """One retrieval round: fusion → diversity → rerank → expansion → packing."""
        fused = await self._hybrid.retrieve(query)
        diverse = apply_diversity(
            fused,
            per_document_cap=self._per_document_cap,
            compare_document_ids=(
                query.document_ids if query.mode is RetrievalMode.COMPARE_DOCUMENTS else None
            ),
        )

        reranked = await self._reranker.rerank(
            query.search_query,
            diverse,
            top_k=min(len(diverse), self._rerank_top_k_max),
        )

        policy = resolve_expansion_policy(query)
        units = await self._expansion.build_units(reranked, policy, query, trace_id=trace_id)

        bundle = build_bundle(units, token_budget=query.context_token_budget, trace_id=trace_id)
        logger.info(
            "retrieval_pipeline_done",
            extra={
                "retrieval_trace_id": bundle.retrieval_trace_id,
                "policy": policy.value,
                "fused": len(fused),
                "diversified": len(diverse),
                "items": len(bundle.items),
                "total_tokens": bundle.total_tokens,
                "documents_used": len(bundle.documents_used),
            },
        )
        return bundle
