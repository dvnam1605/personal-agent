"""Generated documents: outline, approve/write, read, download (.docx / .xlsx)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.infrastructure.db.models import GeneratedDocument
from app.infrastructure.db.session import get_db_session
from app.services.documents.service import (
    DocumentGenerationService,
    DocumentNotFoundError,
    generated_docs_dir,
    outline_message,
)
from app.services.routing.query.parsing import retrieval_requester_id

router = APIRouter(prefix="/documents", tags=["documents"])


class OutlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=3, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


class SectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=400)


class ColumnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    source: str = Field(default="content", max_length=32)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=300)
    sections: list[SectionIn] | None = Field(default=None, max_length=8)
    # Table outlines: edited columns and what one row is.
    columns: list[ColumnIn] | None = Field(default=None, max_length=12)
    row_unit: str | None = Field(default=None, max_length=120)
    multi_rows: bool | None = None


class RewriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: str = Field(min_length=3, max_length=1000)


def _service() -> DocumentGenerationService:
    from app.services.retrieval.factory import build_retrieval_pipeline
    from app.services.retrieval.metadata import MetadataLookupService

    return DocumentGenerationService(
        metadata_service=MetadataLookupService(),
        pipeline=build_retrieval_pipeline(use_viranker=False, per_document_cap=6),
    )


_MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _file_format(row: GeneratedDocument) -> str | None:
    return Path(row.docx_path).suffix.lstrip(".") if row.docx_path else None


def _view(row: GeneratedDocument) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "title": row.title,
        "kind": (row.outline or {}).get("kind", "document"),
        "file_format": _file_format(row),
        "template_key": row.template_key,
        "outline": row.outline,
        "sections": row.sections,
        "citations": row.citations,
        "markdown": row.markdown,
        "has_docx": bool(row.docx_path),
        "error": row.error,
    }


async def _owned(session: AsyncSession, user_id: str, doc_id: str) -> GeneratedDocument:
    try:
        return await DocumentGenerationService.get_owned(session, user_id, doc_id)
    except DocumentNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.") from None


@router.get("", summary="Generated documents of the current user (newest first)")
async def list_documents(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(GeneratedDocument)
            .where(GeneratedDocument.user_id == user_id)
            .order_by(GeneratedDocument.created_at.desc())
            .limit(50)
        )
    ).scalars()
    return [
        {
            "id": r.id,
            "title": r.title,
            "status": r.status,
            "template_key": r.template_key,
            "kind": (r.outline or {}).get("kind", "document"),
            "has_docx": bool(r.docx_path),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/outline", summary="Propose an outline and source documents for a request")
async def create_outline(
    body: OutlineRequest,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    row = await _service().create_outline(
        session,
        user_id=user_id,
        requester_id=retrieval_requester_id(user_id),
        request=body.request,
        conversation_id=body.conversation_id,
    )
    await session.commit()
    return {**_view(row), "message": outline_message(row)}


@router.post("/{doc_id}/approve", summary="Approve (optionally edited) outline and write")
async def approve(
    doc_id: str,
    body: ApproveRequest,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    await _owned(session, user_id, doc_id)
    row = await _service().approve_and_write(
        session,
        user_id=user_id,
        requester_id=retrieval_requester_id(user_id),
        doc_id=doc_id,
        sections=[s.model_dump() for s in body.sections] if body.sections else None,
        title=body.title,
        columns=[c.model_dump() for c in body.columns] if body.columns else None,
        row_unit=body.row_unit,
        multi_rows=body.multi_rows,
    )
    await session.commit()
    return _view(row)


@router.post(
    "/{doc_id}/sections/{index}/rewrite",
    summary="Rewrite one section of a finished document following feedback",
)
async def rewrite_section(
    doc_id: str,
    index: int,
    body: RewriteRequest,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    await _owned(session, user_id, doc_id)
    try:
        row = await _service().rewrite_section(
            session,
            user_id=user_id,
            requester_id=retrieval_requester_id(user_id),
            doc_id=doc_id,
            index=index,
            feedback=body.feedback.strip(),
        )
    except IndexError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found.") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    await session.commit()
    return _view(row)


@router.get("/{doc_id}", summary="Generated document (outline / Markdown / status)")
async def get_document(
    doc_id: str,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    return _view(await _owned(session, user_id, doc_id))


@router.get("/{doc_id}/download", summary="Download the generated .docx / .xlsx")
async def download(
    doc_id: str,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FileResponse:
    row = await _owned(session, user_id, doc_id)
    path = Path(row.docx_path or "")
    # Only serve files from the generated-docs directory, never an arbitrary path.
    base = generated_docs_dir().resolve()
    if not row.docx_path or not path.resolve().is_relative_to(base) or not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not available.")
    safe_title = "".join(ch for ch in row.title if ch.isalnum() or ch in " -_").strip()[:80]
    suffix = path.suffix.lower()
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(suffix, "application/octet-stream"),
        filename=f"{safe_title or 'tai-lieu'}{suffix}",
    )
