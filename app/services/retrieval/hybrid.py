"""Parallel hybrid retrieval fused with Reciprocal Rank Fusion (spec P10-05).

Dense and sparse run concurrently (``asyncio.gather``); each candidate keeps
its per-source ranks and receives ``fusion_score = sum(1 / (k + rank))`` over
the sources that returned it. No cross-score normalization is performed
(spec: avoid ad hoc normalization without evidence).
"""

from __future__ import annotations

import asyncio
import logging

from app.domain.models.retrieval import RetrievalQuery, RetrievedChunk
from app.services.retrieval.dense import DenseRetrievalService
from app.services.retrieval.sparse import SparseRetrievalService

logger = logging.getLogger(__name__)

DEFAULT_RRF_K = 60


class HybridRetrievalService:
    """Runs both retrievers concurrently and fuses their rankings with RRF."""

    def __init__(
        self,
        dense_service: DenseRetrievalService,
        sparse_service: SparseRetrievalService,
        *,
        k: int = DEFAULT_RRF_K,
    ) -> None:
        self._dense = dense_service
        self._sparse = sparse_service
        self._k = k

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]:
        dense_results, sparse_results = await asyncio.gather(
            self._dense.retrieve(query),
            self._sparse.retrieve(query),
        )

        fused: dict[str, tuple[RetrievedChunk | None, RetrievedChunk | None]] = {}
        for chunk in dense_results:
            fused[chunk.chunk_id] = (chunk, None)
        for chunk in sparse_results:
            dense_part, _ = fused.get(chunk.chunk_id, (None, None))
            fused[chunk.chunk_id] = (dense_part, chunk)

        results: list[RetrievedChunk] = []
        for dense_chunk, sparse_chunk in fused.values():
            base = dense_chunk or sparse_chunk
            assert base is not None  # both can never be None by construction
            fusion_score = 0.0
            if dense_chunk is not None:
                fusion_score += 1.0 / (self._k + (dense_chunk.dense_rank or 1))
            if sparse_chunk is not None:
                fusion_score += 1.0 / (self._k + (sparse_chunk.sparse_rank or 1))
            sources = [
                name
                for name, part in (("dense", dense_chunk), ("sparse", sparse_chunk))
                if part is not None
            ]
            results.append(
                RetrievedChunk(
                    chunk_id=base.chunk_id,
                    parent_id=base.parent_id,
                    document_id=base.document_id,
                    content_raw=base.content_raw,
                    score=fusion_score,
                    retrieval_type="hybrid",
                    dense_rank=dense_chunk.dense_rank if dense_chunk else None,
                    sparse_rank=sparse_chunk.sparse_rank if sparse_chunk else None,
                    fusion_score=fusion_score,
                    metadata={**base.metadata, "retrieval_sources": sources},
                )
            )

        results.sort(key=lambda item: item.fusion_score or 0.0, reverse=True)
        logger.debug("hybrid_retrieval_done", extra={"count": len(results)})
        return results
