"""Child-level dense retrieval over pgvector (spec P10-03).

Searches CHILD/TABLE_CHILD embeddings only (``hierarchy_level = 1``), scoped
by the P9D-M3 ownership rule and restricted to ACTIVE document versions.
Scores are pgvector cosine distances (embeddings are L2-normalized, so the
distance ordering equals the similarity ordering); rank 1 = nearest.
"""

from __future__ import annotations

import logging

from app.domain.models.retrieval import RetrievalMode, RetrievalQuery, RetrievedChunk
from app.services.ingestion.embedding import LocalEmbeddingService
from app.services.retrieval.entity_catalog import get_entity_catalog
from app.services.retrieval.provider import RowProvider, SqlAlchemyRowProvider
from app.services.retrieval.query_reformulation import areformulate_query, reformulate_query
from app.services.retrieval.sql import (
    ANCHOR_SELECT_COLUMNS,
    CANDIDATE_FILTERS_TEMPLATE,
    SEARCHABLE_LEVEL,
    chunk_anchor_metadata,
    document_scope_sql,
    owner_scope_sql,
    vector_literal,
)

logger = logging.getLogger(__name__)

_DENSE_SQL_TEMPLATE = f"""
SELECT c.id AS chunk_id,
       c.parent_id AS parent_id,
       c.document_id AS document_id,
       c.content_raw AS content_raw,
{ANCHOR_SELECT_COLUMNS},
       (c.embedding <=> '{{vector}}'::vector) AS score
{{filters}}
ORDER BY c.embedding <=> '{{vector}}'::vector
LIMIT {{limit}}
"""


class DenseRetrievalService:
    """Embeds the search query locally and fetches nearest CHILD chunks."""

    def __init__(
        self,
        embedding_service: LocalEmbeddingService,
        provider: RowProvider | None = None,
    ) -> None:
        self._embedding = embedding_service
        self._provider: RowProvider = provider or SqlAlchemyRowProvider()

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]:
        await get_entity_catalog().ensure_loaded()
        cleaned_query, _ = await areformulate_query(query.search_query)
        vector = await self._embedding.embed_query(cleaned_query)
        filters = CANDIDATE_FILTERS_TEMPLATE.format(
            level=SEARCHABLE_LEVEL,
            owner_scope=owner_scope_sql(query.requester_id),
            document_scope=document_scope_sql(
                query.document_ids if query.mode is not RetrievalMode.CORPUS_SEARCH else []
            ),
        )
        sql = _DENSE_SQL_TEMPLATE.format(
            vector=vector_literal(vector),
            filters=filters,
            limit=query.top_k_dense,
        )
        rows = await self._provider.fetch(sql)
        results = [
            RetrievedChunk(
                chunk_id=str(row["chunk_id"]),
                parent_id=str(row["parent_id"]) if row["parent_id"] else None,
                document_id=str(row["document_id"]),
                content_raw=row["content_raw"],
                score=float(row["score"]),
                retrieval_type="dense",
                dense_rank=rank,
                metadata=chunk_anchor_metadata(row),
            )
            for rank, row in enumerate(rows, start=1)
        ]
        logger.debug("dense_retrieval_done", extra={"count": len(results)})
        return results
