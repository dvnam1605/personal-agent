"""Source selection and outline planning for generated documents.

Metadata filters and RAG relevance pick the source documents; one optional LLM
call then proposes an outline that follows the sources' own structure.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.core.llm import llm_json
from app.core.text import unaccent_vietnamese
from app.domain.models.retrieval import RetrievalMode, RetrievalQuery
from app.services.documents.tables.writer import (
    DEFAULT_COLUMNS,
    TABLE_MAX_SOURCES,
    normalize_columns,
    propose_table,
)
from app.services.documents.templates import (
    TABLE_TEMPLATE_KEY,
    DocTemplate,
    select_template,
)
from app.services.documents.writing.models import (
    MAX_SECTIONS,
    DocumentOutline,
    OutlineSection,
    _plain_summary,
    _subject,
    _topic,
)

logger = logging.getLogger(__name__)

MAX_SOURCES = 8
MAX_CANDIDATES = 50
MAX_OUTLINE_SNIPPETS = 16
MAX_FULL_OUTLINE_SOURCES = 3
MAX_OUTLINE_LINES = 90
MAX_OUTLINE_HEADINGS = 30
OUTLINE_TIMEOUT_SECONDS = 45.0


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
