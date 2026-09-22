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

import json
import re
import uuid
from typing import Any

SEARCHABLE_LEVEL = 1  # CHILD and TABLE_CHILD rows (spec P10-03/04)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


def uuid_literal(value: str) -> str:
    """Canonical quoted UUID literal; raises ValueError on invalid input."""
    return f"'{uuid.UUID(value)}'"


def identifier_literal(value: str) -> str:
    """Canonical quoted identifier literal; validates format to prevent SQL injection."""
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid identifier literal: {value!r}")
    return f"'{value}'"


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
    literals = ", ".join(identifier_literal(document_id) for document_id in document_ids)
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
       d.source_type AS source_type,
       d.uri AS uri,
       d.title AS filename,
       d.id AS document_version_id,
       d.version_number AS version_number,
       c.node_type AS node_type,
       c.heading_path AS heading_path,
       c.chunk_index AS chunk_index,
       c.page_start AS page_start,
       c.page_end AS page_end,
       c.citation_label AS citation_label\
"""


def preferred_filename(*sources: dict[str, Any]) -> str | None:
    """Prefer a human filename (title) over a storage URI."""
    for src in sources:
        if not src:
            continue
        for key in ("filename", "document_title", "title"):
            value = src.get(key)
            if value not in (None, ""):
                return str(value)
        uri = src.get("uri")
        if uri not in (None, ""):
            return str(uri)
    return None


def chunk_anchor_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Citation-ready source anchors carried on every candidate (spec P10-03).

    Rows come from the retriever SQL which selects ``ANCHOR_SELECT_COLUMNS``;
    missing/NULL keys are tolerated (older fixtures, partial columns) and
    dropped from the emitted metadata dict.
    """
    anchors = {
        "document_title": row.get("document_title"),
        "source_type": row.get("source_type"),
        "uri": row.get("uri"),
        "filename": preferred_filename(row),
        "document_version_id": row.get("document_version_id") or row.get("document_id"),
        "version_number": row.get("version_number"),
        "node_type": row.get("node_type"),
        "heading_path": coerce_heading_list(row.get("heading_path")),
        "chunk_index": row.get("chunk_index"),
        "page_start": row.get("page_start"),
        "page_end": row.get("page_end"),
        "citation_label": row.get("citation_label"),
    }
    return {key: value for key, value in anchors.items() if value not in (None, [], "")}


def coerce_heading_list(value: Any) -> list[str]:
    """Heading paths arrive as JSON lists, already-parsed lists or NULL."""
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


PARENT_FETCH_TEMPLATE = """
SELECT c.id AS chunk_id,
       c.document_id AS document_id,
       c.content_raw AS content_raw,
       c.heading_path AS heading_path,
       c.page_start AS page_start,
       c.page_end AS page_end,
       c.citation_label AS citation_label,
       d.title AS document_title,
       d.source_type AS source_type,
       d.uri AS uri,
       d.title AS filename,
       d.id AS document_version_id,
       d.version_number AS version_number
FROM document_chunks c
JOIN documents d ON c.document_id = d.id
WHERE c.hierarchy_level = 0
  AND {parent_scope}
  AND d.is_active
  AND {owner_scope}
"""

SIBLING_FETCH_TEMPLATE = """
SELECT c.id AS chunk_id,
       c.parent_id AS parent_id,
       c.document_id AS document_id,
       c.node_type AS node_type,
       c.heading_path AS heading_path,
       c.chunk_index AS chunk_index,
       c.page_start AS page_start,
       c.page_end AS page_end,
       c.citation_label AS citation_label,
       d.title AS document_title,
       d.source_type AS source_type,
       d.uri AS uri,
       d.title AS filename,
       d.id AS document_version_id,
       d.version_number AS version_number,
       c.content_raw AS content_raw
FROM document_chunks c
JOIN documents d ON c.document_id = d.id
WHERE c.hierarchy_level = 1
  AND {sibling_scope}
  AND d.is_active
  AND {owner_scope}
ORDER BY c.parent_id, c.chunk_index
"""


def parent_scope_sql(parent_ids: list[str]) -> str:
    """Restrict a fetch to explicit parent chunk ids; validates every id."""
    if not parent_ids:
        return "FALSE"
    literals = ", ".join(identifier_literal(parent_id) for parent_id in parent_ids)
    return f"c.id IN ({literals})"


def sibling_scope_sql(parent_ids: list[str]) -> str:
    """IN-list over parent ids for sibling windows; validates every id."""
    if not parent_ids:
        return "FALSE"
    literals = ", ".join(identifier_literal(parent_id) for parent_id in parent_ids)
    return f"c.parent_id IN ({literals})"
