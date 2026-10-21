"""Generated-document lifecycle: outline → (user edits/approves) → write → files."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import (
    Document,
    DocumentChunk,
    DocumentProfile,
    GeneratedDocument,
)
from app.services.documents.generation import (
    DocumentOutline,
    DocumentPlanner,
    OutlineSection,
    SectionWriter,
)
from app.services.documents.render import render_docx, render_markdown
from app.services.documents.review import SectionReviewer
from app.services.documents.table import META_SOURCES, TableWriter, normalize_columns
from app.services.documents.table_render import render_table_markdown, render_table_xlsx
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
        self._table_writer = TableWriter(pipeline, review=use_llm and review)

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
    async def profile_metadata(session: AsyncSession, ids: list[str]) -> dict[str, dict[str, str]]:
        """Metadata-backed table cells per document (exact values, no LLM)."""
        if not ids:
            return {}
        rows = (
            await session.execute(
                select(Document, DocumentProfile)
                .outerjoin(DocumentProfile, DocumentProfile.document_id == Document.id)
                .where(Document.id.in_(ids))
            )
        ).all()
        out: dict[str, dict[str, str]] = {}
        for doc, profile in rows:
            entities = (profile.entities if profile is not None else None) or []
            signer = next(
                (
                    str(e.get("name") or "")
                    for e in entities
                    if isinstance(e, dict)
                    and (e.get("role") in {"signer", "signatory"} or e.get("kind") == "signer")
                ),
                "",
            )
            doc_date = profile.doc_date if profile is not None else None
            number = (profile.doc_number if profile is not None else None) or ""
            out[doc.id] = {
                "doc_number": number,
                "doc_date": doc_date.strftime("%d/%m/%Y") if doc_date else "",
                "title": (profile.title if profile is not None else None) or doc.title or "",
                "issuer": (profile.issuer if profile is not None else None) or "",
                "doc_type": (profile.doc_subtype if profile is not None else None) or "",
                "signer": signer,
                # Rows are listed by date, then number.
                "sort_key": f"{doc_date.isoformat() if doc_date else '9999-12-31'}|{number}",
            }
        return out

    @staticmethod
    async def document_texts(
        session: AsyncSession, ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Top-level (parent) chunks of each document, in reading order."""
        if not ids:
            return {}
        rows = (
            await session.execute(
                select(
                    DocumentChunk.document_id,
                    DocumentChunk.id,
                    DocumentChunk.content_raw,
                    DocumentChunk.page_start,
                    DocumentChunk.heading_path,
                )
                .where(DocumentChunk.document_id.in_(ids), DocumentChunk.hierarchy_level == 0)
                .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
            )
        ).all()
        out: dict[str, list[dict[str, Any]]] = {}
        for doc_id, chunk_id, text, page_start, heading_path in rows:
            out.setdefault(doc_id, []).append(
                {
                    "chunk_id": chunk_id,
                    "text": text,
                    "page_start": page_start,
                    "section_title": (heading_path or [None])[-1],
                }
            )
        return out

    @staticmethod
    async def source_outline(session: AsyncSession, ids: list[str]) -> list[str]:
        """Table of contents of the source documents (section headings, in order)."""
        from app.services.documents.generation import MAX_OUTLINE_LINES

        if not ids:
            return []
        rows = (
            await session.execute(
                select(DocumentChunk.document_id, DocumentChunk.heading_path)
                .where(DocumentChunk.document_id.in_(ids), DocumentChunk.hierarchy_level == 0)
                .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
            )
        ).all()
        titles = dict(
            zip(ids, await DocumentGenerationService.source_titles(session, ids), strict=False)
        )
        lines: list[str] = []
        seen: set[tuple[str, str]] = set()
        for doc_id, path in rows:
            heading = " > ".join(h for h in (path or []) if h)
            if not heading or (doc_id, heading) in seen:
                continue
            seen.add((doc_id, heading))
            prefix = f"[{titles.get(doc_id, doc_id)}] " if len(ids) > 1 else ""
            lines.append(f"{prefix}{heading}")
            if len(lines) >= MAX_OUTLINE_LINES:
                break
        return lines

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
            source_outline_for=lambda ids: self.source_outline(session, ids),
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
        columns: list[dict[str, str]] | None = None,
        row_unit: str | None = None,
        multi_rows: bool | None = None,
    ) -> GeneratedDocument:
        row = await self.get_owned(session, user_id, doc_id)
        if row.status not in {"outline", "failed"}:
            return row
        outline = DocumentOutline.model_validate(row.outline)
        if sections and outline.kind != "table":
            # Edited sections keep the source headings the planner mapped to them.
            mapped = {s.heading: s.sources for s in outline.sections}
            edited = [OutlineSection.model_validate(s) for s in sections][:8]
            outline = outline.model_copy(
                update={
                    "sections": [
                        e
                        if e.sources
                        else e.model_copy(update={"sources": mapped.get(e.heading, [])})
                        for e in edited
                    ]
                }
            )
        if columns and outline.kind == "table":
            edited = normalize_columns(columns)
            if edited:
                outline = outline.model_copy(update={"columns": edited})
        if row_unit is not None and row_unit.strip():
            outline = outline.model_copy(update={"row_unit": row_unit.strip()[:120]})
        if multi_rows is not None:
            outline = outline.model_copy(update={"multi_rows": multi_rows})
        if title:
            outline = outline.model_copy(update={"title": title[:300]})
        row.status = "writing"
        row.title = outline.title
        row.outline = outline.model_dump()
        await session.flush()

        try:
            if outline.kind == "table":
                metadata = await self.profile_metadata(session, outline.source_document_ids)
                full_texts = await self.document_texts(session, outline.source_document_ids)
                rows, citations = await self._table_writer.write(
                    outline,
                    row.request,
                    requester_id,
                    metadata=metadata,
                    full_texts=full_texts,
                )
                self._store_table(row, outline, rows, citations)
            else:
                written = await self._writer.write(outline, row.request, requester_id)
                self._store(
                    row, outline, [s.model_dump() for s in written.sections], written.citations
                )
            row.status = "done"
            row.error = None
        except Exception as exc:  # noqa: BLE001 - keep the outline so the user can retry
            logger.exception("document_generation_failed id=%s", row.id)
            row.status = "failed"
            row.error = str(exc)[:500]
        await session.flush()
        return row

    async def rewrite_section(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        requester_id: str | None,
        doc_id: str,
        index: int,
        feedback: str,
    ) -> GeneratedDocument:
        """Rewrite one section of a finished document following the user's feedback."""
        row = await self.get_owned(session, user_id, doc_id)
        if row.status != "done" or not row.sections:
            raise ValueError("Tài liệu chưa viết xong, chưa thể viết lại từng mục.")
        if (row.outline or {}).get("kind") == "table":
            raise ValueError("Bảng không viết lại theo mục; hãy sửa cột rồi tạo bảng mới.")
        if not 0 <= index < len(row.sections):
            raise IndexError(index)
        outline = DocumentOutline.model_validate(row.outline)
        sections, citations = await self._writer.rewrite_section(
            outline,
            row.request,
            requester_id,
            index=index,
            feedback=feedback,
            sections=list(row.sections),
            citations=list(row.citations or []),
        )
        self._store(row, outline, sections, citations)
        await session.flush()
        return row

    @staticmethod
    def _store_table(
        row: GeneratedDocument,
        outline: DocumentOutline,
        rows: list[dict[str, Any]],
        citations: list[dict[str, Any]],
    ) -> None:
        """Persist table rows and render the Markdown preview and the .xlsx."""
        columns = [c.model_dump() for c in outline.columns]
        row.sections = rows
        row.citations = citations
        row.markdown = render_table_markdown(outline.title, columns, rows, citations)
        path = generated_docs_dir() / f"{row.id}.xlsx"
        render_table_xlsx(path, outline.title, columns, rows, citations, request=row.request)
        # The column holds the output file of either kind (.docx or .xlsx).
        row.docx_path = str(path)

    @staticmethod
    def _store(
        row: GeneratedDocument,
        outline: DocumentOutline,
        sections: list[dict[str, Any]],
        citations: list[dict[str, Any]],
    ) -> None:
        """Persist sections/citations and re-render the Markdown and .docx."""
        template = TEMPLATE_BY_KEY.get(outline.template_key) or select_template(row.request)
        row.sections = sections
        row.citations = citations
        row.markdown = render_markdown(outline.title, sections, citations, template=template)
        path = generated_docs_dir() / f"{row.id}.docx"
        render_docx(path, outline.title, sections, citations, template=template)
        row.docx_path = str(path)


def outline_message(row: GeneratedDocument) -> str:
    outline = DocumentOutline.model_validate(row.outline)
    template = TEMPLATE_BY_KEY.get(outline.template_key)
    lines = [
        f"**Dàn ý đề xuất — {template.label if template else 'Tài liệu'}:** {outline.title}",
        "",
    ]
    if outline.kind == "table":
        unit = outline.row_unit or "văn bản"
        lines += [
            f"Mỗi dòng: **{unit}**"
            + (" (một văn bản có thể có nhiều dòng)" if outline.multi_rows else ""),
            "",
            "**Các cột:**",
        ]
        lines += [
            f"{i}. **{c.name}** — {c.description}"
            + (" _(lấy từ thông tin văn bản)_" if c.source in META_SOURCES else "")
            for i, c in enumerate(outline.columns, 1)
        ]
    else:
        lines += [f"{i}. **{s.heading}** — {s.purpose}" for i, s in enumerate(outline.sections, 1)]
    lines += ["", f"**Tài liệu nguồn ({len(outline.source_titles)}):**"]
    lines += [f"- {t}" for t in outline.source_titles] or [
        "- (chưa tìm thấy tài liệu nguồn phù hợp)"
    ]
    what = "lập bảng" if outline.kind == "table" else "viết tài liệu"
    edits = "thêm/bớt cột" if outline.kind == "table" else "thêm/bớt mục"
    lines += [
        "",
        f"Trả lời **“duyệt”** để tôi {what} theo dàn ý này, hoặc gửi yêu cầu chỉnh sửa "
        f"(ví dụ: {edits}, đổi tiêu đề) rồi duyệt.",
    ]
    return "\n".join(lines)


def result_message(row: GeneratedDocument) -> str:
    if row.status != "done":
        return f"Không tạo được tài liệu ({row.error or row.status}). Bạn có thể thử duyệt lại."
    if (row.outline or {}).get("kind") == "table":
        return (row.markdown or "") + table_review_note(row.sections)
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


def table_review_note(rows: list[dict[str, Any]]) -> str:
    """One line on what the cell check verified and cleared."""
    reviews = {r["document_id"]: r.get("review") or {} for r in rows}
    verdicts = [v.get("verdict") for v in reviews.values()]
    cleared = sum(1 for r in rows for c in r.get("cells", []) if c.get("status") == "cleared")
    if not rows:
        return "\n\n> Không trích được dòng nào từ các văn bản nguồn."
    if verdicts and all(v in {"skipped", "metadata"} for v in verdicts):
        base = "\n\n> Các ô lấy từ thông tin văn bản; chưa tự kiểm tra phần trích nội dung."
    else:
        fixed = sum(1 for v in verdicts if v in {"revised", "trimmed"})
        base = (
            f"\n\n> Đã đối chiếu từng ô với văn bản nguồn ({len(reviews)} văn bản"
            + (f", sửa lại {fixed} văn bản" if fixed else "")
            + ")."
        )
    if cleared:
        base += f" {cleared} ô chưa xác minh được đã để trống (tô vàng trong file Excel)."
    return base
