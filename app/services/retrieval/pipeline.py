"""Deterministic retrieval processing flow (P10B): fusion -> diversity ->
rerank -> expansion -> budget packing.

Pure orchestration around the P10A services; every stage is independently
injectable for tests and future strategy overrides. No LLM is involved
anywhere in the path.
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
from app.services.retrieval.diversity import apply_diversity
from app.services.retrieval.expansion import ExpansionService, resolve_expansion_policy
from app.services.retrieval.packing import build_bundle
from app.services.retrieval.provider import RowProvider, SqlAlchemyRowProvider
from app.services.retrieval.rerank import IdentityReranker, Reranker

logger = logging.getLogger(__name__)

# Spec starting range: rerank input ~20-50 children.
DEFAULT_RERANK_TOP_K_MAX = 48


@runtime_checkable
class HybridRetriever(Protocol):
    """Duck-typed seam any fused-candidate source must satisfy."""

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]: ...


class RetrievalPipeline:
    """Composes the full deterministic retrieval stage chain."""

    def __init__(
        self,
        hybrid_service: HybridRetriever,
        provider: RowProvider | None = None,
        *,
        reranker: Reranker | None = None,
        per_document_cap: int | None = None,
        rerank_top_k_max: int = DEFAULT_RERANK_TOP_K_MAX,
    ) -> None:
        self._hybrid = hybrid_service
        self._reranker: Reranker = reranker or IdentityReranker()
        self._expansion = ExpansionService(provider or SqlAlchemyRowProvider())
        self._per_document_cap = per_document_cap
        self._rerank_top_k_max = max(int(rerank_top_k_max), 1)

    async def run(self, query: RetrievalQuery) -> EvidenceBundle:
        """Fusion -> P10-06 hygiene -> P10-07 rerank -> expansion -> packing."""
        trace_id = uuid.uuid4().hex

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
        units = await self._expansion.build_units(reranked, policy, query)

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
