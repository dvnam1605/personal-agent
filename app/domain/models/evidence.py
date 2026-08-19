"""Evidence models for grounded reasoning and traceability."""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import EvidenceType


class EvidenceSource(BaseModel):
    """Source identity metadata preserving provenance of extracted evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: str = Field(
        ...,
        description="Type or origin of the source (e.g. 'gmail_message', 'calendar_event', 'drive_file', 'rag_chunk').",
    )
    source_id: str = Field(
        ...,
        description="Unique identifier in the source system (e.g. email ID, event ID, file ID).",
    )
    uri: str | None = Field(
        default=None,
        description="Canonical URI or web URL to the original source object if available.",
    )
    title: str | None = Field(
        default=None,
        description="Human-readable title or subject of the source item.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary structured metadata specific to the source system.",
    )


class EvidenceItem(BaseModel):
    """Grounded evidence chunk with full provenance tracking."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier of this evidence record.",
    )
    evidence_type: EvidenceType = Field(
        ...,
        description="Domain classification of this evidence.",
    )
    content: str = Field(
        ...,
        description="Extracted factual content or snippet.",
    )
    source: EvidenceSource = Field(
        ...,
        description="Exact origin metadata preserving source identity.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Extraction or relevance confidence score.",
    )
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the evidence was extracted.",
    )

    @field_validator("extracted_at", mode="after")
    @classmethod
    def ensure_utc_aware(cls, v: datetime) -> datetime:
        """Enforce UTC-aware timestamp."""
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("Timestamp must be UTC-aware (e.g. datetime.now(UTC)).")
        return v
