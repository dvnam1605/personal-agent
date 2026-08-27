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
from app.domain.models.documents import (
    DetectedDocumentType,
    SourceDocument,
    TypeDetectionResult,
)
from app.domain.models.parsed_document import (
    ParsedDocument,
    ParseResult,
    ParseStatus,
)
from app.services.ingestion.parsing.quality import evaluate_parse_quality
from app.services.ingestion.type_detection import detect_document_type

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.ingestion.parsing.docling_parser import DoclingDocumentParser
    from app.services.ingestion.parsing.markdown_parser import MarkdownDocumentParser


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
) -> DoclingDocumentParser | MarkdownDocumentParser:
    """Pick the adapter for a detected type; preparsed_markdown always MD."""
    from app.services.ingestion.parsing.docling_parser import DoclingDocumentParser
    from app.services.ingestion.parsing.markdown_parser import MarkdownDocumentParser

    if document_type is DetectedDocumentType.MARKDOWN or source_type == "preparsed_markdown":
        return MarkdownDocumentParser()
    return DoclingDocumentParser()


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
        parser = build_parser_for_type(document_type, source_type=source.source_type)

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
    "parse_source",
]
