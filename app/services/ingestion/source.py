"""Source discovery helpers: checksums and SourceDocument construction (P9A-2)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.models.documents import SourceDocument, SourceType

_READ_CHUNK = 1024 * 1024


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


def build_local_source_document(
    path: Path,
    *,
    source_type: SourceType = "local_fixture",
    source_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SourceDocument:
    """Build a SourceDocument from a local file (fixtures, preparsed markdown).

    The checksum is always computed from content; size and mtime are read from
    the filesystem when available.
    """
    resolved = path.resolve()
    stat_result = resolved.stat()
    modified_at = datetime.fromtimestamp(stat_result.st_mtime)
    return SourceDocument(
        source_id=source_id or resolved.name,
        source_type=source_type,
        filename=resolved.name,
        mime_type=None,
        modified_at=modified_at,
        size_bytes=stat_result.st_size,
        checksum=checksum_file(resolved),
        external_uri=None,
        metadata=metadata or {},
    )
