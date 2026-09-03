"""Retrieval processing pipeline (P10B + P10C).

P10B stages: fusion → diversity → rerank → expansion → budget packing.
P10C stages: sufficiency check → bounded retry → compare-policy audit →
             answer synthesis (optional).

Pure orchestration around the P10A/B services; the only LLM call is in
answer synthesis (``run_with_synthesis``).  The deterministic-only
``run()`` method is unchanged for callers who only need ``EvidenceBundle``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Protocol, runtime_checkable

from app.domain.models.retrieval import (
    EvidenceBundle,
    RetrievalMode,
    RetrievalQuery,
    RetrievedChunk,
)
from app.domain.models.sufficiency import SufficiencyStatus, SufficiencyVerdict
from app.services.retrieval.compare_policy import CompareResult, enforce_compare_diversity
from app.services.retrieval.diversity import apply_diversity
from app.services.retrieval.expansion import ExpansionService, resolve_expansion_policy
from app.services.retrieval.packing import build_bundle
from app.services.retrieval.provider import RowProvider, SqlAlchemyRowProvider
from app.services.retrieval.rerank import IdentityReranker, Reranker
from app.services.retrieval.retry import RetryPolicy, apply_retry_strategy
from app.services.retrieval.sufficiency import SufficiencyChecker
from app.services.retrieval.synthesis import (
    AnswerSynthesizer,
    PromptAnswerSynthesizer,
    SynthesisResult,
)

logger = logging.getLogger(__name__)

# Spec starting range: rerank input ~20-50 children.
DEFAULT_RERANK_TOP_K_MAX = 48


@runtime_checkable
class HybridRetriever(Protocol):
    """Duck-typed seam any fused-candidate source must satisfy."""

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]: ...


class RetrievalPipeline:
    """Composes the full retrieval stage chain (P10B deterministic + P10C policies)."""

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
        self._reranker: Reranker = reranker or IdentityReranker()
        self._expansion = ExpansionService(provider or SqlAlchemyRowProvider())
        self._per_document_cap = per_document_cap
        self._rerank_top_k_max = max(int(rerank_top_k_max), 1)
        # P10C
        self._sufficiency = sufficiency_checker or SufficiencyChecker()
        self._retry_policy = retry_policy or RetryPolicy()
        self._synthesizer: AnswerSynthesizer = synthesizer or PromptAnswerSynthesizer()

    async def run(self, query: RetrievalQuery) -> EvidenceBundle:
        """Fusion → P10-06 hygiene → P10-07 rerank → expansion → packing."""
        trace_id = uuid.uuid4().hex
        bundle = await self._retrieve_and_pack(query, trace_id)
        return bundle

    async def run_with_sufficiency(
        self, query: RetrievalQuery
    ) -> tuple[EvidenceBundle, SufficiencyVerdict, CompareResult | None]:
        """Retrieval + sufficiency check + bounded retry (P10-16/17).

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
            bundle = await self._retrieve_and_pack(
                retry_query, f"{root_trace_id}-att{attempt}"
            )
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
    ) -> SynthesisResult:
        """Full path: retrieval → sufficiency → retry → synthesis (P10-18).

        Preserves PARTIAL signal and propagates missing document warnings
        to synthesis prompt.
        """
        bundle, verdict, compare_result = await self.run_with_sufficiency(query)

        if verdict.status is SufficiencyStatus.INSUFFICIENT:
            return SynthesisResult(
                answer="Tôi không tìm thấy đủ tài liệu nội bộ để trả lời câu hỏi này.",
                status=SufficiencyStatus.INSUFFICIENT,
                citations=[],
            )

        missing_docs: list[str] = list(verdict.missing_document_ids)
        if compare_result and compare_result.missing_documents:
            missing_docs = sorted(set(missing_docs + compare_result.missing_documents))

        return await self._synthesizer.synthesize(
            query.original_query,
            bundle,
            internal_only=internal_only,
            missing_documents=missing_docs if missing_docs else None,
            verdict_status=verdict.status,
        )

    # ------------------------------------------------------------------
    # Internal: single retrieval round
    # ------------------------------------------------------------------

    async def _retrieve_and_pack(
        self, query: RetrievalQuery, trace_id: str
    ) -> EvidenceBundle:
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
