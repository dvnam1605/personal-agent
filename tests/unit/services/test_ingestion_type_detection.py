"""Unit tests for file type detection (spec P9A-4)."""

import pytest

from app.domain.models.documents import (
    DetectedDocumentType,
    DetectionStatus,
)
from app.services.ingestion.type_detection import detect_document_type

_PDF_HEAD = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3"
_ZIP_HEAD = b"PK\x03\x04\x14\x00\x06\x00"
_TEXT_HEAD = "# Heading\n\nSome markdown text.\n"
_BINARY_HEAD = b"\x00\x01\x02\xff\xfe"


class TestSupportedDetection:
    def test_pdf_by_magic_bytes(self) -> None:
        result = detect_document_type("file.pdf", "application/pdf", _PDF_HEAD)
        assert result.status == DetectionStatus.SUPPORTED
        assert result.document_type == DetectedDocumentType.PDF
        assert result.warnings == []

    def test_docx_by_zip_magic(self) -> None:
        result = detect_document_type("file.docx", None, _ZIP_HEAD)
        assert result.document_type == DetectedDocumentType.DOCX

    def test_markdown_by_text_content(self) -> None:
        result = detect_document_type("notes.md", "text/markdown", _TEXT_HEAD)
        assert result.document_type == DetectedDocumentType.MARKDOWN

    def test_markdown_without_content_uses_extension(self) -> None:
        result = detect_document_type("README.md", None, None)
        assert result.document_type == DetectedDocumentType.MARKDOWN
        assert "content unavailable for sniffing" in result.warnings[0]

    def test_mime_semibold_parameter_stripped(self) -> None:
        result = detect_document_type("a.pdf", "application/pdf; charset=utf-8", _PDF_HEAD)
        assert result.document_type == DetectedDocumentType.PDF


class TestSignalMismatches:
    def test_zip_sniff_without_corroboration_is_unsupported(self) -> None:
        """A zip payload named .pdf is ambiguous: typed UNSUPPORTED, not a guess."""
        result = detect_document_type("fake.pdf", None, _ZIP_HEAD)
        assert result.status == DetectionStatus.UNSUPPORTED
        assert any("ambiguous content sniff" in warning for warning in result.warnings)

    def test_mime_lies_about_pdf_content(self) -> None:
        result = detect_document_type("a.docx", "application/pdf", _PDF_HEAD)
        assert result.document_type == DetectedDocumentType.PDF
        assert any("mismatch" in warning for warning in result.warnings)

    def test_plain_text_without_markdown_signal_is_unsupported(self) -> None:
        result = detect_document_type("notes.txt", "text/plain", _TEXT_HEAD)
        assert result.status == DetectionStatus.UNSUPPORTED


class TestUnsupportedDetection:
    @pytest.mark.parametrize(
        ("filename", "mime"),
        [
            ("song.mp3", "audio/mpeg"),
            ("archive.zip", "application/zip"),
            ("image.png", "image/png"),
            ("noext", None),
        ],
    )
    def test_unsupported_inputs_typed_not_raised(self, filename: str, mime: str | None) -> None:
        result = detect_document_type(filename, mime, _BINARY_HEAD)
        assert result.status == DetectionStatus.UNSUPPORTED
        assert result.document_type is None

    def test_binary_content_without_any_signal_is_unsupported(self) -> None:
        result = detect_document_type("blob.bin", None, _BINARY_HEAD)
        assert result.status == DetectionStatus.UNSUPPORTED

    def test_no_content_no_signals_is_unsupported(self) -> None:
        result = detect_document_type("mystery", None, b"")
        assert result.status == DetectionStatus.UNSUPPORTED


class TestZipWithoutDocxName:
    def test_generic_zip_with_docx_extension_is_docx_candidate(self) -> None:
        """PK magic corroborated by the .docx extension maps to DOCX."""
        result = detect_document_type("weird.docx", None, _ZIP_HEAD + _BINARY_HEAD)
        assert result.document_type == DetectedDocumentType.DOCX

    def test_generic_zip_with_zip_mime_is_unsupported(self) -> None:
        result = detect_document_type("archive.zip", "application/zip", _ZIP_HEAD)
        assert result.status == DetectionStatus.UNSUPPORTED
