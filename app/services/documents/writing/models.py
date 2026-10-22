"""Outline and written-document models, and the request-text helpers both stages share.

The outline is what the user approves; the written document is what the writer
returns, section by section, with document-wide citation numbers.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.services.documents.tables.writer import MAX_COLUMNS, TABLE_MAX_SOURCES, TableColumn

MAX_SECTIONS = 8


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
