"""DocumentParser Protocol and the parse_source orchestration entry point.

The flow implements spec P9B-4: normal parse -> useful text sufficient?
-> yes: continue / no: typed NEEDS_OCR (inline OCR is config-gated OFF in
runtime V1; scanned PDFs are queued for the offline OCR path, P9E).

Adapter modules are imported lazily inside functions so this module stays
import-light and free of engine dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.core.config import ParsingSettings
from app.core.config import settings as app_settings
from app.domain.models.ingestion.documents import (
    DetectedDocumentType,
    SourceDocument,
    TypeDetectionResult,
)
from app.domain.models.ingestion.parsed_document import (
    ParsedDocument,
    ParseResult,
    ParseStatus,
)
from app.services.ingestion.parsing.quality import evaluate_parse_quality
from app.services.ingestion.type_detection import detect_document_type

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.ingestion.parsing.docling_parser import DoclingDocumentParser
    from app.services.ingestion.parsing.markdown_parser import MarkdownDocumentParser
    from app.services.ingestion.parsing.pymupdf_parser import PyMuPDFDocumentParser
    from app.services.ingestion.parsing.xlsx_parser import XlsxDocumentParser


class ParserUnavailableError(RuntimeError):
    """Raised when a parser's engine dependency is not installed."""


class DocumentParseError(RuntimeError):
    """Raised when an engine fails to convert one document.

    The orchestrator converts this into a typed CORRUPT ParseResult so one
    broken file never aborts a batch.
    """


@runtime_checkable
class DocumentParser(Protocol):
    """Provider-neutral parsing boundary (spec P9B-1)."""

    async def parse(
        self,
        source: SourceDocument,
        content: bytes,
    ) -> ParsedDocument: ...


def build_parser_for_type(
    document_type: DetectedDocumentType | None,
    *,
    source_type: str,
    content: bytes | None = None,
    settings: ParsingSettings | None = None,
) -> DoclingDocumentParser | MarkdownDocumentParser | PyMuPDFDocumentParser | XlsxDocumentParser:
    """Pick the adapter for a detected type; preparsed_markdown always MD.

    PDFs with a text layer may go to PyMuPDF (see ``ParsingSettings.pdf_engine``);
    that needs ``content`` to inspect the file.
    """
    from app.services.ingestion.parsing.docling_parser import DoclingDocumentParser
    from app.services.ingestion.parsing.markdown_parser import MarkdownDocumentParser

    if document_type is DetectedDocumentType.MARKDOWN or source_type == "preparsed_markdown":
        return MarkdownDocumentParser()
    if document_type is DetectedDocumentType.XLSX:
        from app.services.ingestion.parsing.xlsx_parser import XlsxDocumentParser

        return XlsxDocumentParser()
    if document_type is DetectedDocumentType.PDF and content is not None:
        from app.services.ingestion.parsing.pymupdf_parser import (
            PyMuPDFDocumentParser,
            prefer_pymupdf,
        )

        settings = settings or app_settings.parsing
        if prefer_pymupdf(
            content, engine=settings.pdf_engine, min_pages=settings.pymupdf_min_pages
        ):
            return PyMuPDFDocumentParser()
    return DoclingDocumentParser()


def parser_identity(
    source: SourceDocument, content: bytes, *, settings: ParsingSettings | None = None
) -> tuple[str, str]:
    """(parser name, version) that ``parse_source`` will use for this source.

    Known before parsing so it can enter the version fingerprint: switching
    a document's engine re-ingests it.
    """
    if source.source_type == "preparsed_markdown":
        from app.services.ingestion.parsing.markdown_parser import MARKDOWN_PARSER_VERSION

        return "markdown", MARKDOWN_PARSER_VERSION
    detection = detect_document_type(source.filename, source.mime_type, content[:64])
    parser = build_parser_for_type(
        detection.document_type,
        source_type=source.source_type,
        content=content,
        settings=settings,
    )
    if type(parser).__name__ == "XlsxDocumentParser":
        from app.services.ingestion.parsing.xlsx_parser import openpyxl_version

        return "openpyxl", openpyxl_version()
    if type(parser).__name__ == "PyMuPDFDocumentParser":
        from app.services.ingestion.parsing.pymupdf_parser import pymupdf_version

        return "pymupdf", pymupdf_version()
    from app.services.ingestion.parsing.docling_parser import docling_version

    return "docling", docling_version()


async def parse_source(
    source: SourceDocument,
    content: bytes,
    *,
    settings: ParsingSettings | None = None,
    detection: TypeDetectionResult | None = None,
) -> ParseResult:
    """Detect -> parse -> quality-gate one source into a typed ParseResult."""
    settings = settings or app_settings.parsing

    if source.source_type == "preparsed_markdown":
        # Preparsed sources (P9E output shape) skip type detection entirely;
        # their content contract is Markdown by definition.
        parser = build_parser_for_type(None, source_type=source.source_type)
        document_type = None
    else:
        detection = detection or detect_document_type(
            source.filename, source.mime_type, content[:64]
        )
        document_type = detection.document_type
        if document_type is None:
            return ParseResult(
                status=ParseStatus.UNSUPPORTED,
                source_id=source.source_id,
                failure_reason=f"unsupported input: {source.filename}",
            )
        parser = build_parser_for_type(
            document_type, source_type=source.source_type, content=content, settings=settings
        )

    try:
        parsed = await parser.parse(source, content)
    except DocumentParseError as exc:
        return ParseResult(
            status=ParseStatus.CORRUPT,
            source_id=source.source_id,
            failure_reason=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - per-file isolation is the contract
        return ParseResult(
            status=ParseStatus.CORRUPT,
            source_id=source.source_id,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )

    report, status, failure_reason = evaluate_parse_quality(
        parsed,
        source_size_bytes=len(content),
        settings=settings,
        ocr_capable_parse=document_type is DetectedDocumentType.PDF,
    )
    if status is ParseStatus.NEEDS_OCR:
        if settings.ocr_fallback_enabled:
            warnings = (
                *report.parse_warnings,
                "ocr_fallback_enabled=true has no effect yet: no inline OCR adapter "
                "exists in the runtime; the engine ships with the offline path (P9E)",
            )
        else:
            warnings = (*report.parse_warnings, "queued for offline OCR path (P9E)")
        report = report.model_copy(update={"parse_warnings": warnings})

    return ParseResult(
        status=status,
        source_id=source.source_id,
        document=parsed,
        quality=report,
        failure_reason=failure_reason if status is not ParseStatus.PARSED else None,
    )


__all__ = [
    "DocumentParseError",
    "DocumentParser",
    "ParserUnavailableError",
    "build_parser_for_type",
    "parser_identity",
    "parse_source",
]
