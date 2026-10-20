"""Run audit endpoints: the evidence a knowledge answer was based on."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.infrastructure.db.evidence_repository import run_evidence
from app.infrastructure.db.models import AssistantRun
from app.infrastructure.db.session import get_db_session

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get(
    "/{run_id}/evidence",
    summary="Evidence units shown to the model for a run, and which were cited",
)
async def get_run_evidence(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    run = await session.get(AssistantRun, run_id)
    # Same 404 for "missing" and "not yours": run ids must not be probeable.
    if run is None or run.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    rows = await run_evidence(session, run_id)
    return {
        "run_id": run_id,
        "retrieval_trace_id": run.retrieval_trace_id,
        "answer_status": run.answer_status,
        "evidence": [
            {
                "label": r.label,
                "rank": r.rank,
                "evidence_id": r.evidence_id,
                "document_id": r.document_id,
                "document_version_id": r.document_version_id,
                "version_number": r.version_number,
                "title": r.title,
                "kind": r.kind,
                "chunk_ids": r.chunk_ids,
                "score": r.score,
                "cited": r.cited,
                "citation_number": r.citation_number,
                "content_hash": r.content_hash,
                "prompt_text": r.prompt_text,
            }
            for r in rows
        ],
    }
