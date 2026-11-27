"""Shared factories for the retrieval policy tests: queries, evidence, bundles, chunks and pipeline fakes."""

from __future__ import annotations

import uuid
from typing import Any

from app.domain.models.retrieval import (
    Evidence,
    EvidenceBundle,
    EvidenceUnitKind,
    RetrievalQuery,
    RetrievedChunk,
)


def make_query(**overrides: Any) -> RetrievalQuery:
    return RetrievalQuery(original_query="van ban tieng Viet", **overrides)


def make_evidence(
    *,
    eid: str | None = None,
    doc: str = "doc-1",
    kind: EvidenceUnitKind = "CHILD",
    content: str = "some content",
    anchors: dict[str, Any] | None = None,
    title: str | None = None,
    heading_path: list[str] | None = None,
    source_type: str | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=eid or uuid.uuid4().hex,
        kind=kind,
        content_raw=content,
        token_estimate=10,
        primary_chunk_id=f"chunk-{uuid.uuid4().hex[:8]}",
        document_id=doc,
        heading_path=heading_path or [],
        anchors=anchors or {},
        title=title,
        source_type=source_type,
    )


def make_bundle(
    items: list[Evidence] | None = None,
    docs: list[str] | None = None,
) -> EvidenceBundle:
    items = items or []
    return EvidenceBundle(
        items=items,
        total_tokens=sum(e.token_estimate for e in items),
        documents_used=docs or sorted({e.document_id for e in items}),
        parent_ids_used=[],
        retrieval_trace_id=uuid.uuid4().hex,
    )


def chunk(
    cid: str | None = None,
    score: float = 0.5,
    *,
    doc: str | None = None,
    parent: str | None = None,
    content: str | None = None,
) -> RetrievedChunk:
    resolved_cid = cid or uuid.uuid4().hex
    return RetrievedChunk(
        chunk_id=resolved_cid,
        parent_id=parent,
        document_id=doc or uuid.uuid4().hex,
        content_raw=content if content is not None else f"noi dung {resolved_cid}",
        score=score,
        retrieval_type="hybrid",
        metadata={},
    )


class FixedHybrid:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.call_count = 0

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]:
        self.call_count += 1
        return self.chunks


class FakeProvider:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows if rows is not None else []

    async def fetch(self, sql: str) -> list[dict]:
        return list(self.rows)
