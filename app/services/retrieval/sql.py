"""Shared SQL building blocks for retrieval services.

Every dynamic value is strictly validated before interpolation:
- identifiers/ids go through :func:`uuid_literal` (parses via ``uuid.UUID``,
  so anything non-UUID raises before it ever reaches SQL);
- the query vector is rendered from Python floats only;
- the FTS query text is bound as a single quoted literal consumed by
  ``websearch_to_tsquery`` (never concatenated into tsquery syntax).

The candidate WHERE clause is the single source of truth for the P9D-M3
ownership rule (``user_id = requester OR user_id IS NULL``) and the
active-version guard.
"""

from __future__ import annotations

import uuid
from typing import Any

SEARCHABLE_LEVEL = 1  # CHILD and TABLE_CHILD rows (spec P10-03/04)


def uuid_literal(value: str) -> str:
    """Canonical quoted UUID literal; raises ValueError on invalid input."""
    return f"'{uuid.UUID(value)}'"


def vector_literal(vector: list[float]) -> str:
    """Render an embedding as a pgvector string literal from floats only."""
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"


def fts_query_expression(search_query: str, dictionary: str = "public.vietnamese_simple") -> str:
    """Escape a single-quote-safe literal for websearch_to_tsquery."""
    escaped = search_query.replace("'", "''")
    return f"websearch_to_tsquery('{dictionary}', '{escaped}')"


def owner_scope_sql(requester_id: str | None) -> str:
    """P9D-M3 ownership rule; NULL requester sees only shared documents."""
    if requester_id is None:
        return "d.user_id IS NULL"
    return f"(d.user_id = {uuid_literal(requester_id)} OR d.user_id IS NULL)"


def document_scope_sql(document_ids: list[str]) -> str:
    """Optional per-document restriction for DOCUMENT_SEARCH / COMPARE modes."""
    if not document_ids:
        return "TRUE"
    literals = ", ".join(uuid_literal(document_id) for document_id in document_ids)
    return f"d.id IN ({literals})"


CANDIDATE_FILTERS_TEMPLATE = """
FROM document_chunks c
JOIN documents d ON c.document_id = d.id
WHERE c.hierarchy_level = {level}
  AND d.is_active
  AND {owner_scope}
  AND {document_scope}
"""


ANCHOR_SELECT_COLUMNS = """\
       d.title AS document_title,
       c.chunk_index AS chunk_index,
       c.page_start AS page_start,
       c.page_end AS page_end,
       c.citation_label AS citation_label\
"""


def chunk_anchor_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Citation-ready source anchors carried on every candidate (spec P10-03).

    Rows come from the retriever SQL which selects ``ANCHOR_SELECT_COLUMNS``;
    missing/NULL keys are tolerated (older fixtures, partial columns) and
    dropped from the emitted metadata dict.
    """
    anchors = {
        "document_title": row.get("document_title"),
        "chunk_index": row.get("chunk_index"),
        "page_start": row.get("page_start"),
        "page_end": row.get("page_end"),
        "citation_label": row.get("citation_label"),
    }
    return {key: value for key, value in anchors.items() if value is not None}
