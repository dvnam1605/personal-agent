"""File type detection combining extension, declared MIME, and content sniffing.

Detection never trusts a single signal and never raises for unsupported input;
it returns a typed UNSUPPORTED result so one bad file cannot abort a batch
(spec P9A-4).
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from app.domain.models.ingestion.documents import (
    DetectedDocumentType,
    DetectionStatus,
    TypeDetectionResult,
)

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"

_PDF_EXTENSIONS = {".pdf"}
_DOCX_EXTENSIONS = {".docx"}
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}

_PDF_MIME_TYPES = {"application/pdf"}
_DOCX_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
_MARKDOWN_MIME_TYPES = {
    "text/markdown",
    "text/x-markdown",
}


def _sniff(content_head: bytes | str | None) -> DetectedDocumentType | None:
    """Best-effort content sniffing from the leading bytes of a file."""
    if not content_head:
        return None
    if isinstance(content_head, str):
        with contextlib.suppress(UnicodeDecodeError):
            content_head = content_head.encode("utf-8")
        if isinstance(content_head, str):
            return DetectedDocumentType.MARKDOWN
    if content_head.startswith(_PDF_MAGIC):
        return DetectedDocumentType.PDF
    if content_head.startswith(_ZIP_MAGIC):
        return DetectedDocumentType.DOCX
    with contextlib.suppress(UnicodeDecodeError):
        if b"\x00" not in content_head and content_head.decode("utf-8"):
            return DetectedDocumentType.MARKDOWN
    return None


def _extension_of(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return suffix


def _vote(
    filename: str,
    mime_type: str | None,
    sniffed: DetectedDocumentType | None,
) -> tuple[DetectedDocumentType | None, list[str]]:
    """Combine the three signals; magic bytes win ties, warnings note conflicts.

    ZIP-derived DOCX and text-derived MARKDOWN sniffs are ambiguous (xlsx/
    pptx/odt are all zips; any text decodes), so they only decide when an
    independent extension or MIME signal corroborates them. The %PDF magic is
    specific and stands alone. An ambiguous sniff that nothing corroborates
    yields UNSUPPORTED instead of a guess (spec P9A-4).
    """
    extension = _extension_of(filename)
    warnings: list[str] = []

    ext_type: DetectedDocumentType | None = None
    if extension in _PDF_EXTENSIONS:
        ext_type = DetectedDocumentType.PDF
    elif extension in _DOCX_EXTENSIONS:
        ext_type = DetectedDocumentType.DOCX
    elif extension in _MARKDOWN_EXTENSIONS:
        ext_type = DetectedDocumentType.MARKDOWN

    mime_type_normalized = (mime_type or "").split(";")[0].strip().lower()
    mime_doc: DetectedDocumentType | None = None
    if mime_type_normalized in _PDF_MIME_TYPES:
        mime_doc = DetectedDocumentType.PDF
    elif mime_type_normalized in _DOCX_MIME_TYPES:
        mime_doc = DetectedDocumentType.DOCX
    elif mime_type_normalized in _MARKDOWN_MIME_TYPES:
        mime_doc = DetectedDocumentType.MARKDOWN

    if sniffed in (DetectedDocumentType.DOCX, DetectedDocumentType.MARKDOWN):
        agrees = sniffed in (ext_type, mime_doc)
        if not agrees:
            # Ambiguous container/text sniff with no independent signal agreeing:
            # either nothing else is known, or the name/MIME contradicts it.
            # Either way, refuse to guess (typed UNSUPPORTED).
            warnings.append("ambiguous content sniff without name/MIME corroboration; ignored")
            return None, warnings

    candidates = [signal for signal in (sniffed, ext_type, mime_doc) if signal]
    if not candidates:
        return None, warnings

    winner = candidates[0]
    for candidate in set(candidates):
        if candidate is not winner:
            warnings.append(
                f"signal mismatch: content suggests {winner.value}, "
                f"another signal suggests {candidate.value}"
            )
    return winner, warnings


def detect_document_type(
    filename: str,
    mime_type: str | None = None,
    content_head: bytes | str | None = None,
) -> TypeDetectionResult:
    """Classify one file as PDF/DOCX/Markdown or typed-UNSUPPORTED."""
    sniffed = _sniff(content_head)
    winner, warnings = _vote(filename, mime_type, sniffed)
    if winner is None:
        return TypeDetectionResult(
            status=DetectionStatus.UNSUPPORTED,
            document_type=None,
            warnings=warnings,
        )
    if sniffed is None:
        warnings.append("content unavailable for sniffing; decided on name/MIME only")
    return TypeDetectionResult(
        status=DetectionStatus.SUPPORTED,
        document_type=winner,
        warnings=warnings,
    )
