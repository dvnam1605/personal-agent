"""Ingestion orchestration contracts (P9D).

Typed statuses, the observability record every job exposes, and the stored
fingerprint state that makes idempotency queryable end-to-end.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class IngestionStatus(StrEnum):
    """Lifecycle of one ingestion job (spec P9D-3)."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PARSING = "PARSING"
    BUILDING_PARENTS = "BUILDING_PARENTS"
    BUILDING_CHILDREN = "BUILDING_CHILDREN"
    EMBEDDING = "EMBEDDING"
    PERSISTING = "PERSISTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NEEDS_OCR = "NEEDS_OCR"


class IngestionObservability(BaseModel):
    """Every field a completed/failed job must expose (spec P9D-5)."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    source_id: str
    document_id: str | None = None
    document_version: int | None = None
    status: IngestionStatus
    source_size_bytes: int = 0

    parser_name: str | None = None
    parser_version: str | None = None
    parent_chunker_version: str | None = None
    child_chunker_version: str | None = None
    embedding_model: str | None = None

    parse_duration_seconds: float = 0.0
    parent_build_duration_seconds: float = 0.0
    child_build_duration_seconds: float = 0.0
    embedding_duration_seconds: float = 0.0
    persist_duration_seconds: float = 0.0

    parent_count: int = 0
    child_count: int = 0
    table_child_count: int = 0

    warnings: tuple[str, ...] = ()
    failure_reason: str | None = None


class StoredFingerprintState(BaseModel):
    """What persistence remembers about the latest version of a source.

    ``fingerprint`` is None for legacy rows predating migration 0006; the
    orchestrator treats those as MODIFIED so they reingest as a new version
    instead of colliding at version 1 (review L1).
    """

    model_config = ConfigDict(frozen=True)

    logical_document_id: str
    document_id: str
    version_number: int = Field(ge=1)
    fingerprint: str | None = None
    is_active: bool = False
