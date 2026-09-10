"""Source discovery helpers: checksums and SourceDocument construction (P9A-2)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.models.ingestion.documents import SourceDocument, SourceType

_READ_CHUNK = 1024 * 1024
SIDECAR_SUFFIX = ".ocr.json"


def checksum_bytes(content: bytes) -> str:
    """SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(content).hexdigest()


def checksum_file(path: Path) -> str:
    """Streaming SHA-256 so large PDFs never load fully into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _is_valid_ocr_sidecar(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("dry_run") is True:
        return False
    checksum = data.get("source_checksum")
    if (
        not isinstance(checksum, str)
        or len(checksum) != 64
        or not all(c in "0123456789abcdefABCDEF" for c in checksum)
    ):
        return False
    engine = data.get("engine")
    if not isinstance(engine, str) or not engine.strip():
        return False
    device = data.get("device")
    if not isinstance(device, str) or not device.strip():
        return False
    status = data.get("status")
    if status is not None and status != "SUCCESS":
        return False
    return True


def _find_ocr_sidecar(path: Path) -> dict[str, Any] | None:
    """Check for companion .ocr.json next to a markdown file and validate its schema."""
    candidates = [
        path.with_suffix(SIDECAR_SUFFIX),
        path.parent / (path.stem + SIDECAR_SUFFIX),
    ]
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if _is_valid_ocr_sidecar(data):
                    return data
            except (OSError, ValueError):
                continue
    return None


def build_local_source_document(
    path: Path,
    *,
    source_type: SourceType = "local_fixture",
    source_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SourceDocument:
    """Build a SourceDocument from a local file (fixtures, preparsed markdown).

    The checksum is always computed from content; size and mtime are read from
    the filesystem with UTC timezone. Companion OCR sidecars are loaded when present.
    """
    resolved = path.resolve()
    stat_result = resolved.stat()
    modified_at = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC)
    doc_metadata = dict(metadata or {})
    if "ocr_sidecar" not in doc_metadata:
        sidecar = _find_ocr_sidecar(resolved)
        if sidecar:
            doc_metadata["ocr_sidecar"] = sidecar

    return SourceDocument(
        source_id=source_id or resolved.name,
        source_type=source_type,
        filename=resolved.name,
        mime_type=None,
        modified_at=modified_at,
        size_bytes=stat_result.st_size,
        checksum=checksum_file(resolved),
        external_uri=None,
        metadata=doc_metadata,
    )
