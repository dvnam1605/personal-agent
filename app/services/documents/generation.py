"""Outline planning and evidence-grounded section writing.

Budget: one optional LLM call for the outline, one per section. Every
section is written by the normal RAG pipeline restricted to the selected
source documents, so it inherits the [E#] citation, grounding and
abstention rules; a section without evidence is marked for the user to
complete instead of being invented.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.http import llm_http_timeout
from app.core.jsonutil import extract_json_object
from app.domain.models.retrieval import RetrievalMode, RetrievalQuery
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.services.documents.templates import TEMPLATE_BY_KEY, DocTemplate, select_template

logger = logging.getLogger(__name__)

MAX_SECTIONS = 8
MAX_SOURCES = 8
OUTLINE_TIMEOUT_SECONDS = 45.0
SECTION_CONTEXT_TOKENS = 6000

MISSING_SECTION_NOTE = (
    "_(Chưa đủ thông tin trong các tài liệu nguồn để viết mục này — cần bổ sung.)_"
)


class OutlineSection(BaseModel):
    heading: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=400)


class DocumentOutline(BaseModel):
    template_key: str
    title: str = Field(min_length=1, max_length=300)
    sections: list[OutlineSection] = Field(min_length=1, max_length=MAX_SECTIONS)
    source_document_ids: list[str] = Field(default_factory=list, max_length=MAX_SOURCES)
    source_titles: list[str] = Field(default_factory=list)


class WrittenSection(BaseModel):
    heading: str
    text: str
    status: str
    citation_numbers: list[int] = Field(default_factory=list)


class WrittenDocument(BaseModel):
    sections: list[WrittenSection]
    citations: list[dict[str, Any]]


def _topic(request: str) -> str:
    """The request minus the 'viết/soạn … báo cáo' verb phrase."""
    text = re.sub(
        r"(?i)^\s*(?:hãy\s+|giúp\s+(?:tôi|mình|em)\s+|làm ơn\s+)*"
        r"(?:viết|soạn(?:\s+thảo)?|tạo|lập|xây dựng|chuẩn bị|tổng hợp thành|làm)\s+(?:cho\s+tôi\s+)?(?:một\s+|1\s+)?",
        "",
        request.strip(),
    )
    return text.strip(" .?!") or request.strip()


async def _llm_json(system: str, user: str, *, timeout: float) -> dict[str, Any] | None:
    api_key = str(settings.llm.openai_api_key or "")
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client:
            resp = await client.post(
                settings.llm.chat_completions_url(),
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": settings.llm.primary_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content") or ""
        return extract_json_object(content)
    except Exception as exc:  # noqa: BLE001 - the template outline is the fallback
        logger.warning("document_outline_llm_failed error=%s", exc)
        return None


class DocumentPlanner:
    """Selects source documents and proposes an outline for approval."""

    def __init__(self, metadata_service: Any, pipeline: Any, *, use_llm: bool = True) -> None:
        self._metadata = metadata_service
        self._pipeline = pipeline
        self._use_llm = use_llm

    async def select_sources(self, request: str, requester_id: str | None) -> list[str]:
        plan = await self._metadata.plan(request, requester_id)
        if plan.spec.has_selector or plan.spec.categories:
            ids, _ = await self._metadata.resolve_document_ids(
                plan.spec, requester_id, cap=MAX_SOURCES
            )
            if ids:
                return ids
        bundle = await self._pipeline.run(
            RetrievalQuery(
                original_query=_topic(request),
                search_query=_topic(request),
                requester_id=requester_id,
                top_k_dense=40,
                top_k_sparse=40,
            )
        )
        ordered: list[str] = []
        for item in bundle.items:
            if item.document_id not in ordered:
                ordered.append(item.document_id)
        return ordered[:MAX_SOURCES]

    async def plan(
        self,
        request: str,
        requester_id: str | None,
        *,
        source_titles_for: Any = None,
    ) -> DocumentOutline:
        template = select_template(request)
        sources = await self.select_sources(request, requester_id)
        titles = list(await source_titles_for(sources)) if source_titles_for and sources else []
        topic = _topic(request)
        default_title = (
            topic[:1].upper() + topic[1:]
            if topic.lower().startswith(template.label.lower())
            else (f"{template.label}: {topic}")
        )
        sections = [OutlineSection(heading=s.heading, purpose=s.purpose) for s in template.sections]
        title = default_title[:300]

        if self._use_llm:
            proposed = await self._llm_outline(template, request, titles)
            if proposed:
                title = str(proposed.get("title") or title)[:300]
                llm_sections = [
                    OutlineSection(
                        heading=str(s.get("heading", "")).strip()[:200],
                        purpose=str(s.get("purpose", "")).strip()[:400],
                    )
                    for s in proposed.get("sections") or []
                    if isinstance(s, dict) and str(s.get("heading", "")).strip()
                ]
                if llm_sections:
                    sections = llm_sections[:MAX_SECTIONS]
        return DocumentOutline(
            template_key=template.key,
            title=title,
            sections=sections,
            source_document_ids=sources,
            source_titles=titles,
        )

    async def _llm_outline(
        self, template: DocTemplate, request: str, titles: list[str]
    ) -> dict[str, Any] | None:
        skeleton = "\n".join(f"- {s.heading}: {s.purpose}" for s in template.sections)
        sources = "\n".join(f"- {t}" for t in titles[:MAX_SOURCES]) or "(chưa xác định)"
        system = (
            "Bạn lập dàn ý cho tài liệu nội bộ tiếng Việt. Giữ cấu trúc mẫu của loại tài liệu, "
            "chỉ điều chỉnh tên/mục đích mục cho sát yêu cầu; tối đa "
            f"{MAX_SECTIONS} mục. Chỉ trả về JSON."
        )
        user = (
            f"Loại tài liệu: {template.label}\nYêu cầu: {request}\n\n"
            f"Cấu trúc mẫu:\n{skeleton}\n\nTài liệu nguồn sẽ dùng:\n{sources}\n\n"
            'Trả về: {"title": "...", "sections": [{"heading": "...", "purpose": "..."}]}'
        )
        return await _llm_json(system, user, timeout=OUTLINE_TIMEOUT_SECONDS)


class SectionWriter:
    """Writes each approved section from evidence in the source documents."""

    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    async def write(
        self, outline: DocumentOutline, request: str, requester_id: str | None
    ) -> WrittenDocument:
        template = TEMPLATE_BY_KEY.get(outline.template_key) or select_template(request)
        global_citations: list[dict[str, Any]] = []
        number_by_key: dict[tuple[str, str], int] = {}
        written: list[WrittenSection] = []

        for section in outline.sections:
            question = (
                f'Viết nội dung mục "{section.heading}" ({section.purpose}) cho {template.label} '
                f'"{outline.title}". Yêu cầu gốc: {request}. Văn phong: {template.tone}. '
                "Chỉ dùng thông tin trong bằng chứng, gắn nhãn trích dẫn cho từng ý; "
                "không lặp lại tiêu đề mục; dùng đoạn văn hoặc gạch đầu dòng."
            )
            query = RetrievalQuery(
                original_query=question,
                search_query=f"{section.heading} {section.purpose} {_topic(request)}",
                mode=RetrievalMode.DOCUMENT_SEARCH
                if outline.source_document_ids
                else RetrievalMode.CORPUS_SEARCH,
                document_ids=list(outline.source_document_ids),
                requester_id=requester_id,
                top_k_dense=30,
                top_k_sparse=30,
                context_token_budget=SECTION_CONTEXT_TOKENS,
                require_source_diversity=len(outline.source_document_ids) > 1,
            )
            try:
                result = await self._pipeline.run_with_synthesis(query, internal_only=True)
            except Exception:  # noqa: BLE001 - one failed section must not lose the rest
                logger.exception("document_section_failed heading=%s", section.heading)
                result = None

            # Only text the model actually grounded counts: the LLM-failure
            # fallback lists related documents but cites nothing.
            grounded = result is not None and any(
                row.get("cited") for row in getattr(result, "evidence", []) or []
            )
            if result is None or not result.citations or not grounded:
                written.append(
                    WrittenSection(
                        heading=section.heading, text=MISSING_SECTION_NOTE, status="missing"
                    )
                )
                continue

            local_to_global: dict[int, int] = {}
            for local, citation in enumerate(result.citations, start=1):
                key = (citation.document_id, citation.chunk_id or citation.evidence_id)
                if key not in number_by_key:
                    number_by_key[key] = len(global_citations) + 1
                    global_citations.append(
                        {
                            "number": number_by_key[key],
                            "document_id": citation.document_id,
                            "document_version_id": citation.document_version_id,
                            "title": citation.title,
                            "chunk_id": citation.chunk_id,
                            "page_start": citation.page_start,
                            "section_title": citation.section_title,
                        }
                    )
                local_to_global[local] = number_by_key[key]
            text = re.sub(
                r"\[(\d+)\]",
                lambda m, mapping=local_to_global: (
                    f"[{mapping.get(int(m.group(1)), int(m.group(1)))}]"
                ),
                result.answer,
            )
            status = "partial" if result.status is not SufficiencyStatus.SUFFICIENT else "ok"
            written.append(
                WrittenSection(
                    heading=section.heading,
                    text=text.strip(),
                    status=status,
                    citation_numbers=sorted(set(local_to_global.values())),
                )
            )
        return WrittenDocument(sections=written, citations=global_citations)
