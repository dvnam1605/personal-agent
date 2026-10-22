"""Unit tests for the parse_source orchestration entry point (spec P9B-4)."""

from __future__ import annotations

import pytest

from app.core.config import ParsingSettings
from app.domain.models.ingestion.documents import SourceDocument
from app.domain.models.ingestion.parsed_document import ParseStatus
from app.services.ingestion.parsing import base as parsing_base
from app.services.ingestion.parsing.base import (
    DocumentParseError,
    build_parser_for_type,
    parse_source,
)
from app.services.ingestion.parsing.docling_parser import DoclingDocumentParser
from app.services.ingestion.parsing.markdown_parser import MarkdownDocumentParser
from tests.unit.services._repo import repo_root

REPO_ROOT = repo_root()
MD_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "parsing" / "markdown" / "preparsed_sample.md"
DOCX_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "parsing" / "docx" / "headings_paragraphs.docx"
UNSUPPORTED_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "parsing" / "failure" / "unsupported.bin"


def _source(**overrides: object) -> SourceDocument:
    values: dict[str, object] = {
        "source_id": "s-1",
        "source_type": "upload",
        "filename": "doc.md",
    }
    values.update(overrides)
    return SourceDocument(**values)  # type: ignore[arg-type]


async def test_markdown_source_parses_end_to_end() -> None:
    result = await parse_source(_source(), MD_FIXTURE.read_bytes())
    assert result.status is ParseStatus.PARSED
    assert result.document is not None
    assert result.quality is not None
    assert result.quality.node_count > 0
    assert result.failure_reason is None


async def test_unsupported_input_typed_not_raised() -> None:
    result = await parse_source(
        _source(filename="blob.bin", mime_type="application/octet-stream"),
        UNSUPPORTED_FIXTURE.read_bytes(),
    )
    assert result.status is ParseStatus.UNSUPPORTED
    assert "unsupported input" in (result.failure_reason or "")


async def test_preparsed_markdown_forces_md_parser_even_with_pdf_name() -> None:
    """preparsed_markdown bypasses detection entirely (no UNSUPPORTED risk)."""
    source = _source(
        source_type="preparsed_markdown",
        filename="scanned-output.pdf",
        mime_type="application/pdf",
    )
    body = "Body text that is definitely long enough to pass the useful-text gate."
    result = await parse_source(source, f"# Title\n\n{body}".encode())
    assert result.status is ParseStatus.PARSED
    assert result.document is not None
    assert result.document.metadata.parser_name == "markdown"


async def test_engine_failure_becomes_corrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(self: DoclingDocumentParser, source: SourceDocument, content: bytes):
        raise DocumentParseError("docling failed on doc.pdf")

    monkeypatch.setattr(DoclingDocumentParser, "parse", boom)
    result = await parse_source(_source(filename="doc.pdf"), b"%PDF-broken")
    assert result.status is ParseStatus.CORRUPT
    assert "docling failed on doc.pdf" in (result.failure_reason or "")


async def test_unexpected_exception_isolated_per_file(monkeypatch: pytest.MonkeyPatch) -> None:
    async def kaboom(self: DoclingDocumentParser, source: SourceDocument, content: bytes):
        raise ValueError("surprise")

    monkeypatch.setattr(DoclingDocumentParser, "parse", kaboom)
    result = await parse_source(_source(filename="doc.pdf"), b"%PDF-x")
    assert result.status is ParseStatus.CORRUPT
    assert "ValueError" in (result.failure_reason or "")


class TestOcrFallbackPolicy:
    async def test_needs_ocr_queued_when_inline_fallback_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.domain.models.ingestion.parsed_document import (
            ParsedDocument,
            ParsedDocumentMetadata,
        )

        async def empty_parse(self: DoclingDocumentParser, source: SourceDocument, content: bytes):
            return ParsedDocument(
                metadata=ParsedDocumentMetadata(
                    source_id=source.source_id,
                    filename=source.filename,
                    parser_name="docling",
                    parser_version="test",
                ),
            )

        monkeypatch.setattr(DoclingDocumentParser, "parse", empty_parse)
        settings = ParsingSettings(ocr_fallback_enabled=False)
        result = await parse_source(_source(filename="scan.pdf"), b"%PDF-scan", settings=settings)
        assert result.status is ParseStatus.NEEDS_OCR
        assert result.quality is not None
        warnings = result.quality.parse_warnings
        assert any("queued for offline OCR path (P9E)" in warning for warning in warnings)

    async def test_no_queue_warning_when_inline_fallback_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.domain.models.ingestion.parsed_document import (
            ParsedDocument,
            ParsedDocumentMetadata,
        )

        async def empty_parse(self: DoclingDocumentParser, source: SourceDocument, content: bytes):
            return ParsedDocument(
                metadata=ParsedDocumentMetadata(
                    source_id=source.source_id,
                    filename=source.filename,
                    parser_name="docling",
                    parser_version="test",
                ),
            )

        monkeypatch.setattr(DoclingDocumentParser, "parse", empty_parse)
        settings = ParsingSettings(ocr_fallback_enabled=True)
        result = await parse_source(_source(filename="scan.pdf"), b"%PDF-scan", settings=settings)
        assert result.status is ParseStatus.NEEDS_OCR
        assert result.quality is not None
        assert all("queued for offline OCR" not in w for w in result.quality.parse_warnings)
        assert any(
            "no inline OCR adapter" in w and "has no effect yet" in w
            for w in result.quality.parse_warnings
        )


async def test_real_docx_fixture_through_pipeline() -> None:
    source = _source(
        source_id="docx-e2e",
        source_type="local_fixture",
        filename="headings_paragraphs.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    result = await parse_source(source, DOCX_FIXTURE.read_bytes())
    assert result.status is ParseStatus.PARSED
    assert result.document is not None
    headings = [node for node in result.document.nodes if node.node_type.value == "heading"]
    assert len(headings) >= 3


def test_build_parser_routes_by_detected_type() -> None:
    from app.domain.models.ingestion.documents import DetectedDocumentType

    assert isinstance(
        build_parser_for_type(DetectedDocumentType.MARKDOWN, source_type="upload"),
        MarkdownDocumentParser,
    )
    assert isinstance(
        build_parser_for_type(DetectedDocumentType.PDF, source_type="upload"), DoclingDocumentParser
    )
    assert isinstance(
        build_parser_for_type(DetectedDocumentType.DOCX, source_type="upload"),
        DoclingDocumentParser,
    )


def test_protocol_satisfied_by_both_adapters() -> None:
    assert isinstance(MarkdownDocumentParser(), parsing_base.DocumentParser)
    assert isinstance(DoclingDocumentParser(), parsing_base.DocumentParser)
