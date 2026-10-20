"""Persistence for per-run evidence snapshots (``run_evidence``)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import AssistantRun, RunEvidence

_ROW_FIELDS = (
    "label",
    "rank",
    "evidence_id",
    "kind",
    "primary_chunk_id",
    "chunk_ids",
    "document_id",
    "document_version_id",
    "version_number",
    "title",
    "prompt_text",
    "content_hash",
    "score",
    "cited",
    "citation_number",
)


async def record_run_evidence(
    session: AsyncSession,
    run_id: str,
    *,
    evidence: list[dict[str, Any]] | None,
    retrieval_trace_id: str | None,
    answer_status: str | None,
) -> int:
    """Store the snapshot and link the run to its trace/status. Returns row count."""
    run = await session.get(AssistantRun, run_id)
    if run is not None:
        run.retrieval_trace_id = retrieval_trace_id
        run.answer_status = answer_status
    rows = 0
    for row in evidence or []:
        session.add(
            RunEvidence(
                run_id=run_id,
                retrieval_trace_id=retrieval_trace_id,
                **{k: row.get(k) for k in _ROW_FIELDS if k in row},
            )
        )
        rows += 1
    await session.flush()
    return rows


async def run_evidence(
    session: AsyncSession, run_id: str, *, cited_only: bool = False
) -> list[RunEvidence]:
    stmt = select(RunEvidence).where(RunEvidence.run_id == run_id)
    if cited_only:
        stmt = stmt.where(RunEvidence.cited.is_(True)).order_by(RunEvidence.citation_number)
    else:
        stmt = stmt.order_by(RunEvidence.rank)
    return list((await session.execute(stmt)).scalars().all())


def citation_payload(row: RunEvidence) -> dict[str, Any]:
    """Citation dict for the UI, rebuilt from the stored snapshot."""
    return {
        "evidence_id": row.evidence_id,
        "document_id": row.document_id,
        "document_version_id": row.document_version_id,
        "version_number": row.version_number,
        "title": row.title or "",
        "chunk_id": row.primary_chunk_id,
        "text": row.prompt_text,
        "score": row.score,
        "citation_number": row.citation_number,
        "citation_type": "chunk",
    }
