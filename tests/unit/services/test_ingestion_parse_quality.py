"""Unit tests for parse-quality gating (spec P9B-3)."""

from __future__ import annotations

import pytest

from app.core.config import ParsingSettings
from app.domain.models.parsed_document import (
    ParagraphNode,
    ParsedDocument,
    ParsedDocumentMetadata,
    ParseStatus,
    SourceAnchor,
)
from app.services.ingestion.parsing.quality import evaluate_parse_quality

SETTINGS = ParsingSettings()
_LONG = "x" * 100


def _document(
    texts: list[str],
    *,
    parser_name: str = "docling",
    page_count: int | None = None,
    ocr_used: bool = False,
    warnings: tuple[str, ...] = (),
) -> ParsedDocument:
    nodes = tuple(
        ParagraphNode(node_id=f"n{i:04d}", order=i, text=text) for i, text in enumerate(texts)
    )
    return ParsedDocument(
        metadata=ParsedDocumentMetadata(
            source_id="s",
            filename="f.pdf",
            parser_name=parser_name,
            parser_version="1",
            page_count=page_count,
            ocr_used=ocr_used,
            parse_warnings=warnings,
        ),
        nodes=nodes,
    )


class TestHappyPath:
    def test_sufficient_text_is_parsed(self) -> None:
        report, status, reason = evaluate_parse_quality(
            _document([_LONG]),
            source_size_bytes=5_000,
            settings=SETTINGS,
            ocr_capable_parse=True,
        )
        assert status is ParseStatus.PARSED
        assert reason is None
        assert report.node_count == 1
        assert report.text_length == len(_LONG)

    def test_report_counts_and_warning_passthrough(self) -> None:
        document = _document([_LONG, "b" * 40], warnings=("engine hint",), ocr_used=True)
        report, status, _ = evaluate_parse_quality(
            document, source_size_bytes=10_000, settings=SETTINGS
        )
        assert status is ParseStatus.PARSED
        assert report.text_length == len(_LONG) + 40
        assert report.parse_warnings == ("engine hint",)
        assert report.ocr_used is True


class TestBrokenParses:
    def test_large_source_with_no_text_queues_ocr_for_pdf(self) -> None:
        report, status, reason = evaluate_parse_quality(
            _document([""], parser_name="docling"),
            source_size_bytes=SETTINGS.large_source_bytes + 1,
            settings=SETTINGS,
            ocr_capable_parse=True,
        )
        assert status is ParseStatus.NEEDS_OCR
        assert "large source" in (reason or "")
        assert report.node_count == 1

    def test_large_source_without_ocr_capability_is_corrupt(self) -> None:
        _, status, _ = evaluate_parse_quality(
            _document([""], parser_name="markdown"),
            source_size_bytes=SETTINGS.large_source_bytes + 1,
            settings=SETTINGS,
            ocr_capable_parse=False,
        )
        assert status is ParseStatus.CORRUPT

    def test_short_but_valid_document_passes(self) -> None:
        """Tiny valid docs are not false-positived into NEEDS_OCR (review L4)."""
        _, status, reason = evaluate_parse_quality(
            _document(["ok"], parser_name="docling"), source_size_bytes=500, settings=SETTINGS
        )
        assert status is ParseStatus.PARSED
        assert reason is None

    def test_small_scanned_pdf_with_empty_tree_needs_ocr(self) -> None:
        _, status, reason = evaluate_parse_quality(
            _document([], parser_name="docling"),
            source_size_bytes=500,
            settings=SETTINGS,
            ocr_capable_parse=True,
        )
        assert status is ParseStatus.NEEDS_OCR
        assert reason == "empty document tree"

    def test_empty_markdown_tree_is_corrupt_not_needs_ocr(self) -> None:
        _, status, reason = evaluate_parse_quality(
            _document([], parser_name="markdown"), source_size_bytes=50, settings=SETTINGS
        )
        assert status is ParseStatus.CORRUPT
        assert reason == "empty document tree"

    def test_extreme_garbage_is_corrupt_before_text_gate(self) -> None:
        garbage = ("\ufffd" * 90) + "ab"
        _, status, reason = evaluate_parse_quality(
            _document([garbage]), source_size_bytes=1_000, settings=SETTINGS
        )
        assert status is ParseStatus.CORRUPT
        assert "garbage" in (reason or "")


class TestPageAccounting:
    def test_empty_pages_computed_from_anchors(self) -> None:
        document = _document([_LONG], page_count=3)
        node = document.nodes[0].model_copy(
            update={"anchor": SourceAnchor(page_start=1, page_end=2)}
        )
        document = document.model_copy(update={"nodes": (node,)})
        report, status, _ = evaluate_parse_quality(
            document, source_size_bytes=9_999, settings=SETTINGS
        )
        assert status is ParseStatus.PARSED
        assert report.empty_page_count == 1


@pytest.mark.parametrize("ratio_setting", [0.0, 0.5], ids=["strict", "lenient"])
def test_garbage_ratio_threshold_configurable(ratio_setting: float) -> None:
    settings = ParsingSettings(garbage_char_ratio=ratio_setting)
    text = "\ufffd\ufffdclean"
    _, status, _ = evaluate_parse_quality(
        _document([text]), source_size_bytes=100, settings=settings
    )
    assert (status is ParseStatus.CORRUPT) is (ratio_setting == 0.0)
