"""Evidence-grounded section writing for an approved outline.

Each section is written by the RAG pipeline restricted to the selected sources,
inheriting its citation and grounding rules; a section without evidence is
marked missing, never invented.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models.retrieval import RetrievalMode, RetrievalQuery
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.services.documents.templates import (
    TEMPLATE_BY_KEY,
    DocTemplate,
    select_template,
    style_for,
)
from app.services.documents.writing.figures import Figure, figure_hint
from app.services.documents.writing.formulas import Formula, formula_hint
from app.services.documents.writing.models import (
    DocumentOutline,
    OutlineSection,
    WrittenDocument,
    WrittenSection,
    _plain_summary,
    _topic,
)
from app.services.documents.writing.quality import (
    DOCUMENT_WRITER_SYSTEM_PROMPT,
    apply_quality_gates,
    polish_style,
)
from app.services.documents.writing.review import SectionReviewer
from app.services.retrieval.answer.images import restore_images, split_images

logger = logging.getLogger(__name__)

SECTION_CONTEXT_TOKENS = 10000

MISSING_SECTION_NOTE = (
    "_(Chưa đủ thông tin trong các tài liệu nguồn để viết mục này — cần bổ sung.)_"
)


class _Draft(BaseModel):
    """One section as written, citations still numbered locally ([1] = first cited)."""

    section: WrittenSection
    local_citations: dict[int, dict[str, Any]] = Field(default_factory=dict)
    removed_meta: list[str] = Field(default_factory=list)
    removed_unverified: list[str] = Field(default_factory=list)


def _citation_key(citation: dict[str, Any]) -> tuple[str, str]:
    return (
        str(citation.get("document_id") or ""),
        str(citation.get("chunk_id") or citation.get("evidence_id") or ""),
    )


def _globalize(
    draft: _Draft, citations: list[dict[str, Any]], number_by_key: dict[tuple[str, str], int]
) -> WrittenSection:
    """Map a draft's local [n] onto the document-wide citation list (extended in place)."""
    section = draft.section
    if section.status == "missing":
        return section
    local_to_global: dict[int, int] = {}
    for local, citation in draft.local_citations.items():
        key = _citation_key(citation)
        if key not in number_by_key:
            number_by_key[key] = len(citations) + 1
            citations.append({**citation, "number": number_by_key[key]})
        local_to_global[local] = number_by_key[key]
    text = re.sub(
        r"\[(\d+)\]",
        lambda m: f"[{local_to_global.get(int(m.group(1)), int(m.group(1)))}]",
        section.text,
    )
    return section.model_copy(
        update={
            "text": text.strip(),
            "citation_numbers": sorted(set(local_to_global.values())),
        }
    )


def renumber_document(
    sections: list[dict[str, Any]], citations: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Number citations 1..n by first use and drop the ones no section cites any more."""
    by_number = {int(c["number"]): c for c in citations}
    mapping: dict[int, int] = {}
    for section in sections:
        if section.get("status") == "missing":
            continue
        for match in re.finditer(r"\[(\d+)\]", section.get("text") or ""):
            old = int(match.group(1))
            if old in by_number and old not in mapping:
                mapping[old] = len(mapping) + 1
    new_sections: list[dict[str, Any]] = []
    for section in sections:
        if section.get("status") == "missing":
            new_sections.append(section)
            continue
        text = re.sub(
            r"\[(\d+)\]",
            lambda m: f"[{mapping[int(m.group(1))]}]" if int(m.group(1)) in mapping else "",
            section.get("text") or "",
        )
        numbers = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text)})
        new_sections.append({**section, "text": text, "citation_numbers": numbers})
    new_citations = sorted(
        ({**by_number[old], "number": new} for old, new in mapping.items()),
        key=lambda c: c["number"],
    )
    return new_sections, new_citations


class SectionWriter:
    """Writes each approved section from evidence in the source documents."""

    def __init__(self, pipeline: Any, *, reviewer: SectionReviewer | None = None) -> None:
        self._pipeline = pipeline
        self._reviewer = reviewer

    async def write(
        self,
        outline: DocumentOutline,
        request: str,
        requester_id: str | None,
        *,
        formulas: dict[str, Formula] | None = None,
        figures: list[Figure] | None = None,
    ) -> WrittenDocument:
        template = TEMPLATE_BY_KEY.get(outline.template_key) or select_template(request)
        global_citations: list[dict[str, Any]] = []
        number_by_key: dict[tuple[str, str], int] = {}
        written: list[WrittenSection] = []
        removed_meta: list[str] = []
        removed_unverified: list[str] = []
        for section in outline.sections:
            # Sections only see their own evidence, so show what earlier ones said.
            earlier = "\n".join(
                f"- {w.heading}: {_plain_summary(w.text, 600)}"
                for w in written
                if w.status != "missing"
            )
            draft = await self._write_one(
                section,
                outline,
                template,
                request,
                requester_id,
                context=earlier,
                formula_hint=formula_hint(formulas or {}, section.sources),
                figures=figure_hint(figures or [], section.sources),
            )
            removed_meta += draft.removed_meta
            removed_unverified += draft.removed_unverified
            written.append(_globalize(draft, global_citations, number_by_key))
        if removed_meta or removed_unverified:
            logger.info(
                "document_quality_gates removed_meta=%d removed_unverified=%d",
                len(removed_meta),
                len(removed_unverified),
            )
        return WrittenDocument(
            sections=written,
            citations=global_citations,
            removed_unverified=removed_unverified,
        )

    async def rewrite_section(
        self,
        outline: DocumentOutline,
        request: str,
        requester_id: str | None,
        *,
        index: int,
        feedback: str,
        sections: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        formulas: dict[str, Formula] | None = None,
        figures: list[Figure] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Rewrite one section following the user's feedback; the rest is kept.

        Citations are renumbered for the whole document.
        """
        template = TEMPLATE_BY_KEY.get(outline.template_key) or select_template(request)
        current = sections[index]
        target = next(
            (s for s in outline.sections if s.heading == current.get("heading")),
            OutlineSection(heading=str(current.get("heading") or ""), purpose=""),
        )
        others = "\n".join(
            f"- {s['heading']}: {_plain_summary(s.get('text') or '', 600)}"
            for i, s in enumerate(sections)
            if i != index and s.get("status") != "missing"
        )
        previous = (
            _plain_summary(current.get("text") or "", 1500)
            if current.get("status") != "missing"
            else ""
        )
        draft = await self._write_one(
            target,
            outline,
            template,
            request,
            requester_id,
            context=others,
            formula_hint=formula_hint(formulas or {}, target.sources),
            figures=figure_hint(figures or [], target.sources),
            feedback=feedback,
            previous=previous,
        )
        if draft.section.status == "missing" and current.get("status") != "missing":
            # Nothing grounded came back: keep the section the user already had.
            return sections, citations
        number_by_key = {_citation_key(c): int(c["number"]) for c in citations}
        extended = [dict(c) for c in citations]
        new_section = _globalize(draft, extended, number_by_key).model_dump()
        updated = [*sections[:index], new_section, *sections[index + 1 :]]
        return renumber_document(updated, extended)

    async def _write_one(
        self,
        section: OutlineSection,
        outline: DocumentOutline,
        template: DocTemplate,
        request: str,
        requester_id: str | None,
        *,
        context: str = "",
        feedback: str | None = None,
        previous: str = "",
        formula_hint: str = "",
        figures: tuple[str, str] = ("", ""),
    ) -> _Draft:
        all_headings = "; ".join(s.heading for s in outline.sections)
        question = (
            f'Viết nội dung mục "{section.heading}" ({section.purpose}) cho {template.label} '
            f'"{outline.title}". Yêu cầu gốc: {request}. '
            f"Văn phong: {template.tone}. {style_for(template)} "
            f"Các mục của tài liệu: {all_headings}. "
            + (
                f"Các mục khác đã viết (không lặp lại, không mâu thuẫn):\n{context}\n"
                if context
                else ""
            )
            + (f"Bản trước của mục này:\n{previous}\n" if previous else "")
            + (
                "CÔNG THỨC TRONG CÁC MỤC NGUỒN CỦA MỤC NÀY (khi trình bày kỹ thuật tương ứng, "
                "viết một câu dẫn có số hiệu, ví dụ 'điểm hợp nhất được tính theo Công thức "
                "(3.3)', rồi giải thích ý nghĩa; KHÔNG chép nội dung công thức, hệ thống tự "
                f"chèn):\n{formula_hint}\n"
                if formula_hint
                else ""
            )
            + (
                "HÌNH TRONG CÁC MỤC NGUỒN CỦA MỤC NÀY (khi một đoạn trình bày đúng nội dung "
                "một hình — sơ đồ, kiến trúc, giao diện, biểu đồ — viết nhãn của hình, ví dụ "
                "[Hình 4.3], trên một dòng riêng ngay sau đoạn đó; chỉ chọn hình thật sự minh "
                f"họa đoạn văn, mỗi hình một lần):\n{figures[0]}\n"
                if figures[0]
                else ""
            )
            + (
                "GÓP Ý CỦA NGƯỜI DÙNG cho mục này (ưu tiên thực hiện, nhưng vẫn chỉ dùng "
                f"thông tin có trong bằng chứng): {feedback}\n"
                if feedback
                else ""
            )
            + "Chỉ dùng thông tin trong bằng chứng, gắn nhãn trích dẫn cho từng ý; "
            "không lặp lại tiêu đề mục; dùng đoạn văn hoặc gạch đầu dòng. "
            "Không nhận xét rằng tài liệu nguồn thiếu nội dung và không nhắc tới mã tài liệu; "
            "nếu bằng chứng của mục này chưa đủ thì chỉ viết phần có căn cứ."
        )
        search = (
            f"{section.heading} {section.purpose} {' '.join(section.sources)} {_topic(request)}"
        )
        if feedback:
            search = f"{search} {feedback}"
        query = RetrievalQuery(
            original_query=question,
            search_query=search,
            mode=RetrievalMode.DOCUMENT_SEARCH
            if outline.source_document_ids
            else RetrievalMode.CORPUS_SEARCH,
            document_ids=list(outline.source_document_ids),
            requester_id=requester_id,
            top_k_dense=40,
            top_k_sparse=40,
            context_token_budget=SECTION_CONTEXT_TOKENS,
            require_source_diversity=len(outline.source_document_ids) > 1,
        )
        missing = _Draft(
            section=WrittenSection(
                heading=section.heading, text=MISSING_SECTION_NOTE, status="missing"
            )
        )
        try:
            result = await self._pipeline.run_with_synthesis(
                query,
                internal_only=True,
                system_prompt=DOCUMENT_WRITER_SYSTEM_PROMPT,
                **({"extra_images": figures[1]} if figures[1] else {}),
            )
        except Exception:  # noqa: BLE001 - one failed section must not lose the rest
            logger.exception("document_section_failed heading=%s", section.heading)
            return missing

        # The LLM-failure fallback lists related documents but cites nothing.
        evidence_rows = getattr(result, "evidence", []) or []
        if not result.citations or not any(row.get("cited") for row in evidence_rows):
            return missing

        evidence_text = "\n".join(str(row.get("prompt_text") or "") for row in evidence_rows)
        # Gates and review work on the prose; the evidence images go back afterwards.
        prose, images = split_images(result.answer)
        answer, gate = apply_quality_gates(prose, evidence_text)
        answer = polish_style(answer)
        removed_meta = list(gate.removed_meta)
        removed_unverified = list(gate.removed_unverified)

        review = None
        if self._reviewer is not None and answer:
            evidence_by_number = {
                int(row["citation_number"]): str(row.get("prompt_text") or "")
                for row in evidence_rows
                if row.get("citation_number")
            }

            def regate(text: str) -> str:
                cleaned, again = apply_quality_gates(text, evidence_text)
                removed_unverified.extend(again.removed_unverified)
                return polish_style(cleaned)

            answer, outcome = await self._reviewer.review_section(
                heading=section.heading,
                purpose=section.purpose,
                style=f"{template.tone}. {style_for(template)}",
                text=answer,
                evidence=evidence_by_number,
                regate=regate,
            )
            review = outcome.model_dump()
            logger.info(
                "document_section_reviewed heading=%s verdict=%s rounds=%d issues=%d removed=%d",
                section.heading,
                outcome.verdict,
                outcome.rounds,
                len(outcome.issues),
                len(outcome.removed_sentences),
            )
        used = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
        if answer and images:
            answer = restore_images(answer, images)
        if not answer or not used:
            return missing.model_copy(
                update={"removed_meta": removed_meta, "removed_unverified": removed_unverified}
            )

        local_citations = {
            local: {
                "document_id": citation.document_id,
                "document_version_id": citation.document_version_id,
                "title": citation.title,
                "chunk_id": citation.chunk_id,
                "evidence_id": citation.evidence_id,
                "page_start": citation.page_start,
                "section_title": citation.section_title,
            }
            for local, citation in enumerate(result.citations, start=1)
            if local in used  # a citation whose sentence a gate removed is dropped
        }
        status = "partial" if result.status is not SufficiencyStatus.SUFFICIENT else "ok"
        return _Draft(
            section=WrittenSection(
                heading=section.heading, text=answer.strip(), status=status, review=review
            ),
            local_citations=local_citations,
            removed_meta=removed_meta,
            removed_unverified=removed_unverified,
        )
