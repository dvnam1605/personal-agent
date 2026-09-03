"""Pluggable child reranking (spec P10-07).

The contract mirrors the execution spec: rerankers receive the diversified
candidates plus a ``top_k`` budget and MUST return descending-relevance items
carrying ``rerank_model`` / ``rerank_score`` / ``rerank_rank`` provenance so
traces can always explain post-rerank ordering.

V1 ships only :class:`IdentityReranker` (no-op passthrough, ADR-0012's real
ViRanker cross-encoder adapter lands behind this protocol later).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models.retrieval import RetrievedChunk


@runtime_checkable
class Reranker(Protocol):
    """Async reranking seam between fusion and expansion (spec P10-07)."""

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]: ...


class IdentityReranker:
    """No-op reranker preserving fusion order; tags provenance bookkeeping."""

    model_name = "identity:v1"

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        selected = candidates[: max(int(top_k), 0)]
        return [
            chunk.model_copy(
                update={
                    "rerank_model": self.model_name,
                    "pre_rerank_rank": position,
                    "rerank_score": float(chunk.score),
                    "rerank_rank": position,
                }
            )
            for position, chunk in enumerate(selected, start=1)
        ]
