"""Domain models for memory items and memory gating (spec P17-03..P17-05)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import MemoryType


class MemoryItem(BaseModel):
    """Domain model representing a personal memory unit."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the memory item.",
    )
    user_id: str = Field(
        ...,
        description="Owner user identifier.",
    )
    memory_type: MemoryType = Field(
        ...,
        description="Classification of memory (PREFERENCE, EPISODIC, WORKING, etc.).",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Text content of the memory.",
    )
    embedding: list[float] | None = Field(
        default=None,
        description="Optional semantic embedding vector.",
    )
    importance: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Importance weighting from 0.0 (trivial) to 1.0 (vital).",
    )
    last_accessed_at: datetime | None = Field(
        default=None,
        description="Timestamp of last access or retrieval.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary provenance or context metadata.",
    )
    is_stale: bool = Field(
        default=False,
        description="Flag set when memory is superseded or invalidated.",
    )


class MemoryGateDecision(BaseModel):
    """Fast triage decision on whether memory retrieval is required."""

    model_config = ConfigDict(extra="forbid")

    should_retrieve: bool = Field(
        ...,
        description="True if context or memory lookup should be performed.",
    )
    target_memory_types: list[MemoryType] = Field(
        default_factory=list,
        description="Specific memory categories to retrieve if should_retrieve is True.",
    )
    reason: str = Field(
        default="",
        description="Short rationale for the gating decision.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in gating classification.",
    )
