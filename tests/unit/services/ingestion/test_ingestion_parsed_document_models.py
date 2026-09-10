"""Unit tests for the normalized document tree contracts (spec P9B-2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.models.ingestion.parsed_document import (
    HeadingNode,
    ListNode,
    ParagraphNode,
    ParsedDocument,
    ParsedDocumentMetadata,
    ParseResult,
    ParseStatus,
    SourceAnchor,
    TableNode,
)


def _metadata(**overrides: object) -> ParsedDocumentMetadata:
    values: dict[str, object] = {
        "source_id": "src-1",
        "filename": "doc.pdf",
        "parser_name": "docling",
        "parser_version": "2.121.0",
    }
    values.update(overrides)
    return ParsedDocumentMetadata(**values)  # type: ignore[arg-type]


class TestTreeModels:
    def test_discriminated_union_accepts_all_node_kinds(self) -> None:
        document = ParsedDocument(
            metadata=_metadata(),
            nodes=(
                HeadingNode(node_id="n0000", order=0, text="Title", level=1),
                ParagraphNode(node_id="n0001", order=1, text="Body"),
                ListNode(node_id="n0002", order=2, text="a\nb", items=("a", "b")),
                TableNode(
                    node_id="n0003",
                    order=3,
                    text="| h |",
                    markdown="| h |\n| - |",
                    row_count=0,
                    column_count=1,
                ),
            ),
        )
        assert [node.node_type.value for node in document.nodes] == [
            "heading",
            "paragraph",
            "list",
            "table",
        ]

    def test_nodes_are_frozen(self) -> None:
        node = ParagraphNode(node_id="n0000", order=0, text="body")
        with pytest.raises(ValidationError):
            node.text = "mutated"  # type: ignore[misc]

    def test_list_node_requires_items_or_text(self) -> None:
        with pytest.raises(ValidationError):
            ListNode(node_id="n0000", order=0, text="", items=())

    def test_heading_level_bounds(self) -> None:
        with pytest.raises(ValidationError):
            HeadingNode(node_id="n0000", order=0, text="x", level=7)

    def test_anchor_page_bounds(self) -> None:
        anchor = SourceAnchor(page_start=2, page_end=3, block_index=4)
        assert anchor.page_end == 3
        with pytest.raises(ValidationError):
            SourceAnchor(page_start=0)

    def test_metadata_defaults_and_warnings(self) -> None:
        metadata = _metadata(parse_warnings=("w1",), page_count=3, ocr_used=True)
        assert metadata.ocr_used is True
        assert metadata.page_count == 3
        assert metadata.parse_warnings == ("w1",)


class TestParseResultInvariants:
    def test_parsed_requires_document_and_quality(self) -> None:
        from app.domain.models.ingestion.parsed_document import ParseQualityReport

        with pytest.raises(ValidationError):
            ParseResult(status=ParseStatus.PARSED, source_id="s")
        result = ParseResult(
            status=ParseStatus.PARSED,
            source_id="s",
            document=ParsedDocument(metadata=_metadata()),
            quality=ParseQualityReport(
                text_length=10, node_count=1, heading_count=0, table_count=0
            ),
        )
        assert result.status == ParseStatus.PARSED

    def test_unsupported_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            ParseResult(status=ParseStatus.UNSUPPORTED, source_id="s")
        result = ParseResult(
            status=ParseStatus.UNSUPPORTED, source_id="s", failure_reason="unsupported input"
        )
        assert result.failure_reason == "unsupported input"

    def test_corrupt_carries_optional_context(self) -> None:
        result = ParseResult(status=ParseStatus.CORRUPT, source_id="s", failure_reason="boom")
        assert result.document is None
        assert result.quality is None
