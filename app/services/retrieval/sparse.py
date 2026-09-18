"""Child-level sparse retrieval over PostgreSQL FTS (spec P10-04).

Uses the generated ``search_vector`` column (``vietnamese_simple``
configuration with unaccent mapping, created by migration 0007) and
``websearch_to_tsquery`` so user punctuation never breaks tsquery syntax.
Scores follow ``ts_rank_cd``: higher is better; rank 1 = best.
"""

from __future__ import annotations

import logging

from app.domain.models.retrieval import RetrievalMode, RetrievalQuery, RetrievedChunk
from app.services.retrieval.provider import RowProvider, SqlAlchemyRowProvider
from app.services.retrieval.query_reformulation import reformulate_query
from app.services.retrieval.sql import (
    ANCHOR_SELECT_COLUMNS,
    CANDIDATE_FILTERS_TEMPLATE,
    SEARCHABLE_LEVEL,
    chunk_anchor_metadata,
    document_scope_sql,
    fts_query_expression,
    owner_scope_sql,
)

logger = logging.getLogger(__name__)

_SPARSE_SQL_TEMPLATE = f"""
SELECT c.id AS chunk_id,
       c.parent_id AS parent_id,
       c.document_id AS document_id,
       c.content_raw AS content_raw,
{ANCHOR_SELECT_COLUMNS},
       ts_rank_cd(c.search_vector, {{tsquery}}) AS score
{{filters}}
  AND c.search_vector @@ {{tsquery}}
ORDER BY score DESC
LIMIT {{limit}}
"""


class SparseRetrievalService:
    """Lexical search over CHILD/TABLE_CHILD text via PostgreSQL FTS."""

    def __init__(self, provider: RowProvider | None = None) -> None:
        self._provider: RowProvider = provider or SqlAlchemyRowProvider()

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]:
        filters = CANDIDATE_FILTERS_TEMPLATE.format(
            level=SEARCHABLE_LEVEL,
            owner_scope=owner_scope_sql(query.requester_id),
            document_scope=document_scope_sql(
                query.document_ids if query.mode is not RetrievalMode.CORPUS_SEARCH else []
            ),
        )
        _, fts_query = reformulate_query(query.search_query)
        sql = _SPARSE_SQL_TEMPLATE.format(
            tsquery=fts_query_expression(fts_query),
            filters=filters,
            limit=query.top_k_sparse,
        )
        rows = await self._provider.fetch(sql)
        results = [
            RetrievedChunk(
                chunk_id=str(row["chunk_id"]),
                parent_id=str(row["parent_id"]) if row["parent_id"] else None,
                document_id=str(row["document_id"]),
                content_raw=row["content_raw"],
                score=float(row["score"]),
                retrieval_type="sparse",
                sparse_rank=rank,
                metadata=chunk_anchor_metadata(row),
            )
            for rank, row in enumerate(rows, start=1)
        ]
        logger.debug("sparse_retrieval_done", extra={"count": len(results)})
        return results
