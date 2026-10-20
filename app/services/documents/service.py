"""Generated-document lifecycle: outline → (user edits/approves) → write → files."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Document, DocumentProfile, GeneratedDocument
from app.services.documents.generation import (
    DocumentOutline,
    DocumentPlanner,
    OutlineSection,
    SectionWriter,
)
from app.services.documents.render import render_docx, render_markdown
from app.services.documents.review import SectionReviewer
from app.services.documents.templates import TEMPLATE_BY_KEY, select_template

logger = logging.getLogger(__name__)


def generated_docs_dir() -> Path:
    return Path(os.getenv("GENERATED_DOCS_DIR", "storage/generated"))


class DocumentNotFoundError(LookupError):
    pass


class DocumentGenerationService:
    def __init__(
        self,
        *,
        metadata_service: Any,
        pipeline: Any,
        use_llm: bool = True,
        review: bool = True,
    ) -> None:
        self._planner = DocumentPlanner(metadata_service, pipeline, use_llm=use_llm)
        # Every written section is checked against its evidence and fixed.
        reviewer = SectionReviewer() if use_llm and review else None
        self._writer = SectionWriter(pipeline, reviewer=reviewer)

    # ------------------------------------------------------------ queries

    @staticmethod
    async def source_titles(session: AsyncSession, ids: list[str]) -> list[str]:
        if not ids:
            return []
        rows = (
            await session.execute(
                select(Document.id, func.coalesce(DocumentProfile.title, Document.title))
                .outerjoin(DocumentProfile, DocumentProfile.document_id == Document.id)
                .where(Document.id.in_(ids))
            )
        ).all()
        by_id = {r[0]: r[1] for r in rows}
        return [str(by_id.get(i) or i) for i in ids]

    @staticmethod
    async def get_owned(session: AsyncSession, user_id: str, doc_id: str) -> GeneratedDocument:
        row = await session.get(GeneratedDocument, doc_id)
        if row is None or row.user_id != user_id:
            raise DocumentNotFoundError(doc_id)
        return row

    @staticmethod
    async def pending_outline(
        session: AsyncSession, user_id: str, conversation_id: str | None
    ) -> GeneratedDocument | None:
        if not conversation_id:
            return None
        stmt = (
            select(GeneratedDocument)
            .where(
                GeneratedDocument.user_id == user_id,
                GeneratedDocument.conversation_id == conversation_id,
                GeneratedDocument.status == "outline",
            )
            .order_by(GeneratedDocument.created_at.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    # ---------------------------------------------------------- lifecycle

    async def create_outline(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        requester_id: str | None,
        request: str,
        conversation_id: str | None = None,
    ) -> GeneratedDocument:
        outline = await self._planner.plan(
            request,
            requester_id,
            source_titles_for=lambda ids: self.source_titles(session, ids),
        )
        row = GeneratedDocument(
            id=str(uuid.uuid4()),
            user_id=user_id,
            conversation_id=conversation_id,
            template_key=outline.template_key,
            title=outline.title,
            request=request,
            outline=outline.model_dump(),
            source_document_ids=outline.source_document_ids,
            status="outline",
        )
        session.add(row)
        await session.flush()
        return row

    async def approve_and_write(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        requester_id: str | None,
        doc_id: str,
        sections: list[dict[str, str]] | None = None,
        title: str | None = None,
    ) -> GeneratedDocument:
        row = await self.get_owned(session, user_id, doc_id)
        if row.status not in {"outline", "failed"}:
            return row
        outline = DocumentOutline.model_validate(row.outline)
        if sections:
            outline = outline.model_copy(
                update={"sections": [OutlineSection.model_validate(s) for s in sections][:8]}
            )
        if title:
            outline = outline.model_copy(update={"title": title[:300]})
        row.status = "writing"
        row.title = outline.title
        row.outline = outline.model_dump()
        await session.flush()

        template = TEMPLATE_BY_KEY.get(outline.template_key) or select_template(row.request)
        try:
            written = await self._writer.write(outline, row.request, requester_id)
            sections_out = [s.model_dump() for s in written.sections]
            row.sections = sections_out
            row.citations = written.citations
            row.markdown = render_markdown(
                outline.title, sections_out, written.citations, template=template
            )
            path = generated_docs_dir() / f"{row.id}.docx"
            render_docx(path, outline.title, sections_out, written.citations, template=template)
            row.docx_path = str(path)
            row.status = "done"
            row.error = None
        except Exception as exc:  # noqa: BLE001 - keep the outline so the user can retry
            logger.exception("document_generation_failed id=%s", row.id)
            row.status = "failed"
            row.error = str(exc)[:500]
        await session.flush()
        return row


def outline_message(row: GeneratedDocument) -> str:
    outline = DocumentOutline.model_validate(row.outline)
    template = TEMPLATE_BY_KEY.get(outline.template_key)
    lines = [
        f"**Dàn ý đề xuất — {template.label if template else 'Tài liệu'}:** {outline.title}",
        "",
    ]
    lines += [f"{i}. **{s.heading}** — {s.purpose}" for i, s in enumerate(outline.sections, 1)]
    lines += ["", "**Tài liệu nguồn:**"]
    lines += [f"- {t}" for t in outline.source_titles] or [
        "- (chưa tìm thấy tài liệu nguồn phù hợp)"
    ]
    lines += [
        "",
        "Trả lời **“duyệt”** để tôi viết tài liệu theo dàn ý này, hoặc gửi yêu cầu chỉnh sửa "
        "(ví dụ: thêm/bớt mục, đổi tiêu đề) rồi duyệt.",
    ]
    return "\n".join(lines)


def result_message(row: GeneratedDocument) -> str:
    if row.status != "done":
        return f"Không tạo được tài liệu ({row.error or row.status}). Bạn có thể thử duyệt lại."
    missing = [s["heading"] for s in row.sections if s.get("status") == "missing"]
    note = (
        "\n\n> Đã lược khỏi tài liệu (chưa đủ thông tin trong nguồn): " + "; ".join(missing)
        if missing
        else ""
    )
    return (row.markdown or "") + note + review_note(row.sections)


def review_note(sections: list[dict[str, Any]]) -> str:
    """One line on what the self-review checked and changed."""
    reviews = [s["review"] for s in sections if s.get("review")]
    if not reviews:
        return ""
    if all(r.get("verdict") == "skipped" for r in reviews):
        return "\n\n> Chưa tự kiểm tra được nội dung (mô hình không phản hồi)."
    revised = sum(1 for r in reviews if r.get("verdict") in {"revised", "trimmed"})
    removed = sum(len(r.get("removed_sentences") or []) for r in reviews)
    if not revised:
        return "\n\n> Đã tự kiểm tra từng mục với tài liệu nguồn: không phát hiện sai lệch."
    detail = f", loại {removed} câu không có căn cứ" if removed else ""
    return f"\n\n> Đã tự kiểm tra từng mục với tài liệu nguồn: sửa lại {revised} mục{detail}."
