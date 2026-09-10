"""Domain models for entities and entity resolution (spec P17-01, P17-02)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import EntityType


class EntityRecord(BaseModel):
    """Domain model representing a resolved entity (person, document, etc.)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the entity.",
    )
    user_id: str = Field(
        ...,
        description="User identifier owning or accessing this entity.",
    )
    entity_type: EntityType = Field(
        ...,
        description="Classification of entity (PERSON, DOCUMENT, EMAIL, etc.).",
    )
    canonical_name: str = Field(
        ...,
        min_length=1,
        description="Canonical primary name of the entity.",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative names, nicknames, or aliases for lookup.",
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary structured metadata (e.g. email address, phone, role).",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score of resolution or extraction.",
    )
    last_referenced_at: datetime | None = Field(
        default=None,
        description="Timestamp when entity was last referenced in conversation.",
    )


class EntityResolutionResult(BaseModel):
    """Outcome of resolving a surface reference or entity mention."""

    model_config = ConfigDict(extra="forbid")

    query_reference: str = Field(
        ...,
        description="Surface text or phrase extracted from the user query.",
    )
    resolved_entities: list[EntityRecord] = Field(
        default_factory=list,
        description="Primary resolved entities with high confidence.",
    )
    is_ambiguous: bool = Field(
        default=False,
        description="True if multiple entities match with similar confidence.",
    )
    candidate_entities: list[EntityRecord] = Field(
        default_factory=list,
        description="Potential candidates if resolution is ambiguous.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Overall confidence in this resolution.",
    )
    resolution_notes: str = Field(
        default="",
        description="Explanatory notes on how resolution was reached.",
    )
