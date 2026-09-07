"""Provider-neutral document ingestion domain contracts (P9A).

These models define the stable boundary between source discovery, parsing
(P9B), chunking (P9C), and orchestration (P9D). Nothing here knows about
Docling, OCR engines, or storage providers.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SourceType = Literal["drive", "upload", "local_fixture", "preparsed_markdown"]


class DetectedDocumentType(StrEnum):
    """Document types the V1 pipeline understands."""

    PDF = "pdf"
    DOCX = "docx"
    MARKDOWN = "markdown"


class DetectionStatus(StrEnum):
    """Typed detection outcome; never raises for unsupported input."""

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


class IngestDecision(StrEnum):
    """Idempotency state machine outcomes (spec P9A-3).

    RETRY_AFTER_FAILURE implements the FAILED transition: a previously failed
    candidate is re-ingested while the previous valid ACTIVE version is kept
    searchable until the new candidate succeeds.
    """

    NEW = "NEW"
    UNCHANGED = "UNCHANGED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    RETRY_AFTER_FAILURE = "RETRY_AFTER_FAILURE"


class SourceDocument(BaseModel):
    """Source-neutral description of one ingestible file."""

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(min_length=1)
    source_type: SourceType
    filename: str = Field(min_length=1, max_length=512)
    mime_type: str | None = None
    modified_at: datetime | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    checksum: str | None = None
    external_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("filename")
    @classmethod
    def filename_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("filename must not be blank")
        return value


class FingerprintInputs(BaseModel):
    """Every input that must change the fingerprint of an ingestible source.

    Pipeline component versions are injected by configuration so a chunker or
    parser upgrade reindexes affected documents.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str
    checksum: str | None = None
    modified_at: datetime | None = None
    size_bytes: int | None = None
    parser_version: str = "unknown"
    parent_chunker_version: str = "unknown"
    child_chunker_version: str = "unknown"
    embedding_model: str = "unknown"
    embedding_dimensions: int = 1024
    ocr_source_checksum: str | None = None
    ocr_engine: str | None = None
    ocr_engine_version: str | None = None
    ocr_device: str | None = None


class DocumentFingerprint(BaseModel):
    """Stable identity of a source at a point in time."""

    model_config = ConfigDict(frozen=True)

    fingerprint: str = Field(min_length=64, max_length=64)
    inputs: FingerprintInputs


class TypeDetectionResult(BaseModel):
    """Outcome of MIME + extension + content sniffing."""

    model_config = ConfigDict(frozen=True)

    status: DetectionStatus
    document_type: DetectedDocumentType | None = None
    warnings: list[str] = Field(default_factory=list)


class StoredSourceState(BaseModel):
    """What the persistence layer remembers about a previously seen source."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    fingerprint: str
    status: Literal["active", "failed"] = "active"
    deleted: bool = False
