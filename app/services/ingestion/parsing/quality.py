"""Parse-quality gating before chunking (spec P9B-3).

Pure function over a ParsedDocument; no I/O and no engine knowledge. Broken
parses get typed statuses (NEEDS_OCR / CORRUPT) so a batch continues with the
remaining files instead of embedding corrupt output.
"""

from __future__ import annotations

import unicodedata

from app.core.config import ParsingSettings
from app.domain.models.parsed_document import (
    ParsedDocument,
    ParseQualityReport,
    ParseStatus,
)

_REPLACEMENT_CHARS = {"\ufffd", "\x00"}


def _garbage_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(
        1
        for char in text
        if char in _REPLACEMENT_CHARS or unicodedata.category(char) in ("Cc", "Cn")
    )
    return bad / len(text)


def evaluate_parse_quality(
    parsed: ParsedDocument,
    *,
    source_size_bytes: int,
    settings: ParsingSettings,
    ocr_capable_parse: bool = False,
) -> tuple[ParseQualityReport, ParseStatus, str | None]:
    """Return (report, status, failure_reason) for one parsed document.

    ``ocr_capable_parse`` is decided by the orchestrator from the detected
    document type (PDF), not by parser naming. A parse is only rejected for
    insufficient text when the tree is empty or when a large source produced
    almost no text (the scanned-PDF signature); short but valid documents
    pass so tiny notes are not false-positived into NEEDS_OCR.
    """
    warnings = list(parsed.metadata.parse_warnings)
    text_length = sum(len(node.text) for node in parsed.nodes)
    heading_count = sum(1 for node in parsed.nodes if node.node_type.value == "heading")
    table_count = sum(1 for node in parsed.nodes if node.node_type.value == "table")

    page_count = parsed.metadata.page_count or 0
    pages_with_content = {
        page
        for node in parsed.nodes
        if node.anchor is not None and node.anchor.page_start is not None
        for page in range(
            node.anchor.page_start, (node.anchor.page_end or node.anchor.page_start) + 1
        )
    }
    empty_page_count = max(page_count - len(pages_with_content), 0)

    report = ParseQualityReport(
        text_length=text_length,
        node_count=len(parsed.nodes),
        heading_count=heading_count,
        table_count=table_count,
        empty_page_count=empty_page_count,
        ocr_used=parsed.metadata.ocr_used,
        parse_warnings=tuple(warnings),
    )

    ratio = _garbage_ratio("".join(node.text for node in parsed.nodes))
    if ratio > settings.garbage_char_ratio:
        return report, ParseStatus.CORRUPT, f"extreme parser garbage (ratio={ratio:.2f})"

    large_source = source_size_bytes >= settings.large_source_bytes
    empty_tree = not parsed.nodes
    large_source_with_no_text = large_source and text_length < settings.min_useful_text_length
    if empty_tree:
        status = ParseStatus.NEEDS_OCR if ocr_capable_parse else ParseStatus.CORRUPT
        return report, status, "empty document tree"
    if large_source_with_no_text:
        reason = f"large source ({source_size_bytes} bytes) with {text_length} useful chars"
        return report, ParseStatus.NEEDS_OCR if ocr_capable_parse else ParseStatus.CORRUPT, reason

    return report, ParseStatus.PARSED, None
