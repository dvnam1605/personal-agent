"""Outline planning and evidence-grounded section writing.

One optional LLM call plans the outline, then each section is written by the
RAG pipeline restricted to the selected sources, inheriting its citation and
grounding rules; a section without evidence is marked missing, never invented.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.core.llm import llm_json
from app.core.text import unaccent_vietnamese
from app.domain.models.retrieval import RetrievalMode, RetrievalQuery
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.services.documents.tables.writer import (
    DEFAULT_COLUMNS,
    MAX_COLUMNS,
    TABLE_MAX_SOURCES,
    TableColumn,
    normalize_columns,
    propose_table,
)
from app.services.documents.templates import (
    TABLE_TEMPLATE_KEY,
    TEMPLATE_BY_KEY,
    DocTemplate,
    select_template,
    style_for,
)
from app.services.documents.writing.formulas import Formula, formula_hint
from app.services.documents.writing.quality import (
    DOCUMENT_WRITER_SYSTEM_PROMPT,
    apply_quality_gates,
    polish_style,
)
from app.services.documents.writing.review import SectionReviewer
from app.services.retrieval.images import restore_images, split_images

logger = logging.getLogger(__name__)

MAX_SECTIONS = 8
MAX_SOURCES = 8
MAX_CANDIDATES = 50
MAX_OUTLINE_SNIPPETS = 16
MAX_FULL_OUTLINE_SOURCES = 3
MAX_OUTLINE_LINES = 90
MAX_OUTLINE_HEADINGS = 30
OUTLINE_TIMEOUT_SECONDS = 45.0
SECTION_CONTEXT_TOKENS = 10000

MISSING_SECTION_NOTE = (
    "_(Chưa đủ thông tin trong các tài liệu nguồn để viết mục này — cần bổ sung.)_"
)


class OutlineSection(BaseModel):
    heading: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=400)
    # Source-document headings this section draws on; they steer its retrieval.
    sources: list[str] = Field(default_factory=list, max_length=8)


class DocumentOutline(BaseModel):
    template_key: str
    title: str = Field(min_length=1, max_length=300)
    # "document" (prose sections, .docx) or "table" (columns per source, .xlsx).
    kind: str = "document"
    sections: list[OutlineSection] = Field(default_factory=list, max_length=MAX_SECTIONS)
    columns: list[TableColumn] = Field(default_factory=list, max_length=MAX_COLUMNS)
    row_unit: str = Field(default="", max_length=120)
    multi_rows: bool = False
    source_document_ids: list[str] = Field(default_factory=list, max_length=TABLE_MAX_SOURCES)
    source_titles: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_body(self) -> DocumentOutline:
        if self.kind == "table" and not self.columns:
            raise ValueError("a table outline needs at least one column")
        if self.kind != "table" and not self.sections:
            raise ValueError("a document outline needs at least one section")
        return self


class WrittenSection(BaseModel):
    heading: str
    text: str
    status: str
    citation_numbers: list[int] = Field(default_factory=list)
    # Self-review outcome (review.SectionReview dump); None when not reviewed.
    review: dict[str, Any] | None = None


class WrittenDocument(BaseModel):
    sections: list[WrittenSection]
    citations: list[dict[str, Any]]
    # Sentences dropped because a figure in them was not in the evidence.
    removed_unverified: list[str] = Field(default_factory=list)


def _topic(request: str) -> str:
    """The request minus the 'viết/soạn … báo cáo' verb phrase."""
    text = re.sub(
        r"(?i)^\s*(?:hãy\s+|giúp\s+(?:tôi|mình|em)\s+|làm ơn\s+)*"
        r"(?:viết|soạn(?:\s+thảo)?|tạo|lập|xây dựng|chuẩn bị|tổng hợp thành|làm)\s+(?:cho\s+tôi\s+)?(?:một\s+|1\s+)?",
        "",
        request.strip(),
    )
    return text.strip(" .?!") or request.strip()


_SUBJECT_SPLIT = re.compile(r"(?i)\b(?:về|dựa trên|dựa vào|từ các|từ|theo|cho)\s+")
_OUTPUT_KIND = re.compile(
    r"(?i)^(?:một\s+)?(?:báo cáo(?:\s+(?:tuần|tháng|quý|năm|tổng hợp))?|tờ trình|"
    r"biên bản(?:\s+(?:cuộc\s+)?họp)?|kế hoạch|công văn|tóm tắt|tài liệu|văn bản|đề cương|"
    r"(?:file\s+)?excel|bảng(?:\s+(?:tổng hợp|thống kê|kê|tính|danh sách))?)\s+"
)


def _subject(request: str) -> str:
    """What the sources are about: the request minus the output kind, which must not filter sources."""
    topic = _topic(request)
    parts = _SUBJECT_SPLIT.split(topic, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    stripped = _OUTPUT_KIND.sub("", topic, count=1).strip()
    return stripped or topic


def _plain_summary(text: str, limit: int = 300) -> str:
    """First characters of a written section without markup or citation badges."""
    plain = re.sub(r"\[\d+\]|[#*_>`]", "", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:limit] + ("…" if len(plain) > limit else "")


class DocumentPlanner:
    """Selects source documents and proposes an outline for approval."""

    def __init__(self, metadata_service: Any, pipeline: Any, *, use_llm: bool = True) -> None:
        self._metadata = metadata_service
        self._pipeline = pipeline
        self._use_llm = use_llm

    async def select_sources(
        self, request: str, requester_id: str | None, *, limit: int = MAX_SOURCES
    ) -> list[str]:
        """Pick up to ``limit`` documents about the request's subject.

        Metadata filters narrow the candidates; surplus matches are ranked by RAG relevance.
        """
        subject = _subject(request)
        plan = await self._metadata.plan(subject, requester_id)
        candidates: list[str] = []
        if plan.spec.has_selector or plan.spec.categories:
            candidates, _ = await self._metadata.resolve_document_ids(
                plan.spec, requester_id, cap=MAX_CANDIDATES
            )
            if 0 < len(candidates) <= limit:
                return candidates
        query = RetrievalQuery(
            original_query=subject,
            search_query=subject,
            requester_id=requester_id,
            top_k_dense=40,
            top_k_sparse=40,
            mode=RetrievalMode.DOCUMENT_SEARCH if candidates else RetrievalMode.CORPUS_SEARCH,
            document_ids=candidates,
        )
        bundle = await self._pipeline.run(query)
        ordered: list[str] = []
        for item in bundle.items:
            if item.document_id not in ordered:
                ordered.append(item.document_id)
        if candidates:
            # Keep relevance order, then fill with the newest remaining matches.
            ordered += [c for c in candidates if c not in ordered]
        return ordered[:limit]

    async def plan(
        self,
        request: str,
        requester_id: str | None,
        *,
        source_titles_for: Any = None,
        source_outline_for: Any = None,
    ) -> DocumentOutline:
        template = select_template(request)
        is_table = template.key == TABLE_TEMPLATE_KEY
        sources = await self.select_sources(
            request, requester_id, limit=TABLE_MAX_SOURCES if is_table else MAX_SOURCES
        )
        titles = list(await source_titles_for(sources)) if source_titles_for and sources else []
        if is_table:
            return await self._plan_table(template, request, requester_id, sources, titles)
        topic = _topic(request)
        default_title = (
            topic[:1].upper() + topic[1:]
            if topic.lower().startswith(template.label.lower())
            else (f"{template.label}: {topic}")
        )
        sections = [OutlineSection(heading=s.heading, purpose=s.purpose) for s in template.sections]
        title = default_title[:300]

        if self._use_llm:
            snippets, headings = await self._source_snippets(request, requester_id, sources)
            if source_outline_for and 0 < len(sources) <= MAX_FULL_OUTLINE_SOURCES:
                # Few sources: use their whole table of contents, not just the headings hit.
                outline_lines = list(await source_outline_for(sources))
                if outline_lines:
                    headings = outline_lines
            proposed = await self._llm_outline(template, request, titles, snippets, headings)
            if proposed:
                title = str(proposed.get("title") or title)[:300]
                llm_sections = [
                    OutlineSection(
                        heading=str(s.get("heading", "")).strip()[:200],
                        purpose=str(s.get("purpose", "")).strip()[:400],
                        sources=[
                            str(x).strip()[:160]
                            for x in (s.get("sources") or [])
                            if isinstance(x, str) and x.strip()
                        ][:8],
                    )
                    for s in proposed.get("sections") or []
                    if isinstance(s, dict) and str(s.get("heading", "")).strip()
                ]
                if llm_sections:
                    sections = normalize_outline_sections(template, llm_sections)
        return DocumentOutline(
            template_key=template.key,
            title=title,
            sections=sections,
            source_document_ids=sources,
            source_titles=titles,
        )

    async def _plan_table(
        self,
        template: DocTemplate,
        request: str,
        requester_id: str | None,
        sources: list[str],
        titles: list[str],
    ) -> DocumentOutline:
        topic = _topic(request)
        title = topic[:1].upper() + topic[1:]
        columns = list(DEFAULT_COLUMNS)
        row_unit, multi_rows = "văn bản", False
        if self._use_llm:
            snippets, _headings = await self._source_snippets(request, requester_id, sources)
            proposed = await propose_table(request, titles, snippets)
            if proposed:
                title = str(proposed.get("title") or title)
                columns = normalize_columns(proposed.get("columns")) or columns
                row_unit = str(proposed.get("row_unit") or row_unit).strip()[:120]
                multi_rows = bool(proposed.get("multi_rows"))
        return DocumentOutline(
            template_key=template.key,
            title=title[:300],
            kind="table",
            columns=columns,
            row_unit=row_unit,
            multi_rows=multi_rows,
            source_document_ids=sources,
            source_titles=titles,
        )

    async def _source_snippets(
        self, request: str, requester_id: str | None, sources: list[str]
    ) -> tuple[list[str], list[str]]:
        """Excerpts of the sources and their section headings, so the outline follows their structure."""
        if not sources:
            return [], []
        subject = _subject(request)
        try:
            bundle = await self._pipeline.run(
                RetrievalQuery(
                    original_query=subject,
                    search_query=subject,
                    requester_id=requester_id,
                    top_k_dense=40,
                    top_k_sparse=40,
                    mode=RetrievalMode.DOCUMENT_SEARCH,
                    document_ids=sources,
                )
            )
        except Exception as exc:  # noqa: BLE001 - outline still works from titles
            logger.warning("document_outline_snippets_failed error=%s", exc)
            return [], []
        snippets: list[str] = []
        headings: list[str] = []
        for item in bundle.items:
            path = " > ".join(h.strip() for h in getattr(item, "heading_path", None) or [] if h)
            if path and path not in headings and len(headings) < MAX_OUTLINE_HEADINGS:
                headings.append(path)
            if len(snippets) < MAX_OUTLINE_SNIPPETS:
                text = _plain_summary(str(getattr(item, "content_raw", "") or ""), 280)
                if text and text not in snippets:
                    snippets.append(text)
        return snippets, headings

    async def _llm_outline(
        self,
        template: DocTemplate,
        request: str,
        titles: list[str],
        snippets: list[str] | None = None,
        headings: list[str] | None = None,
    ) -> dict[str, Any] | None:
        skeleton = "\n".join(
            f"- {s.heading}: {s.purpose}"
            + (
                "  ← MỤC NỘI DUNG: thay bằng 2–6 mục (càng nhiều chủ đề có tư liệu càng tách nhiều) "
                "đặt tên theo đúng chủ đề/công việc/sự kiện "
                "cụ thể trong tài liệu nguồn"
                if s.topical
                else ""
            )
            for s in template.sections
        )
        sources = "\n".join(f"- {t}" for t in titles[:MAX_SOURCES]) or "(chưa xác định)"
        excerpts = "\n".join(f"- {t}" for t in (snippets or [])) or "(không có)"
        structure = "\n".join(f"- {h}" for h in (headings or [])) or "(không có)"
        system = (
            "Bạn là chuyên viên lập dàn ý văn bản hành chính, báo cáo tiếng Việt.\n"
            "1. Giữ bố cục thông thường của loại văn bản theo cấu trúc mẫu (các phần mở đầu, "
            "đánh giá, kiến nghị… giữ tên quen thuộc của thể loại).\n"
            "2. Mục được đánh dấu MỤC NỘI DUNG phải tách thành các mục có tên cụ thể rút ra từ "
            "nội dung tài liệu nguồn (ví dụ 'II. Công tác khen thưởng thành tích tại Liên hoan "
            "Phát thanh toàn quốc'), mỗi mục một chủ đề có đủ tư liệu.\n"
            "2a. Khi tài liệu nguồn có các mục tương ứng với yêu cầu (xem 'Cấu trúc mục liên "
            "quan'), các mục nội dung phải BÁM THEO và PHỦ ĐỦ các tiểu mục đó (gộp tiểu mục nhỏ "
            "cùng chủ đề nếu cần), không bỏ sót chủ đề nào có trong cấu trúc.\n"
            "3. TUYỆT ĐỐI KHÔNG đặt tiêu đề chung chung như 'Nội dung tổng hợp', 'Tổng hợp nội "
            "dung', 'Các nội dung chính'. Không tạo mục cho chủ đề không có trong tài liệu nguồn.\n"
            f"4. Tối đa {MAX_SECTIONS} mục, đánh số theo kiểu của cấu trúc mẫu, liên tục.\n"
            "Chỉ trả về JSON."
        )
        user = (
            f"Loại tài liệu: {template.label}\nYêu cầu: {request}\n\n"
            f"Cấu trúc mẫu:\n{skeleton}\n\nTài liệu nguồn sẽ dùng:\n{sources}\n\n"
            f"Cấu trúc mục liên quan trong tài liệu nguồn:\n{structure}\n\n"
            f"Trích đoạn nội dung từ tài liệu nguồn:\n{excerpts}\n\n"
            'Trả về: {"title": "...", "sections": [{"heading": "...", "purpose": "...", '
            '"sources": ["tên mục trong tài liệu nguồn mà mục này dựa vào", "..."]}]}'
        )
        return await llm_json(system, user, timeout=OUTLINE_TIMEOUT_SECONDS)


_GENERIC_HEADING = re.compile(
    r"^(?:noi dung tong hop|tong hop noi dung|tong hop chung|noi dung tong hop chung|"
    r"tong hop|cac noi dung tong hop)$"
)
_HEADING_NUMBER = re.compile(
    r"^\s*(?:\d+(?:\.\d+)+\.?|(?:[IVXLC]+|\d+|[a-zđ])[.)])\s+", re.IGNORECASE
)
_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")


def _numbering_style(template: DocTemplate) -> str:
    first = template.sections[0].heading if template.sections else ""
    if re.match(r"^[IVX]+\.\s", first):
        return "roman"
    if re.match(r"^\d+\.\s", first):
        return "arabic"
    return "none"


def normalize_outline_sections(
    template: DocTemplate, sections: list[OutlineSection]
) -> list[OutlineSection]:
    """Conventional headings, consistent numbering, no generic 'Nội dung tổng hợp'.

    Generic headings are renamed to the template's content slot; numbering follows the template.
    """
    topical = next((s for s in template.sections if s.topical), None)
    fallback = _HEADING_NUMBER.sub("", topical.heading) if topical else "Kết quả thực hiện"
    style = _numbering_style(template)
    out: list[OutlineSection] = []
    for section in sections[:MAX_SECTIONS]:
        bare = _HEADING_NUMBER.sub("", section.heading).strip().rstrip(":")
        if _GENERIC_HEADING.match(unaccent_vietnamese(bare).lower().strip(" .")):
            bare = fallback
        if not bare or any(_HEADING_NUMBER.sub("", s.heading) == bare for s in out):
            continue
        n = len(out) + 1
        prefix = {"roman": f"{_ROMAN[n - 1]}. ", "arabic": f"{n}. "}.get(style, "")
        out.append(
            OutlineSection(
                heading=(prefix + bare)[:200], purpose=section.purpose, sources=section.sources
            )
        )
    return out or [OutlineSection(heading=s.heading, purpose=s.purpose) for s in template.sections]


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
                query, internal_only=True, system_prompt=DOCUMENT_WRITER_SYSTEM_PROMPT
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
