"""Domain models for token pressure and context compaction (spec P17-08..P17-10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class CompactionTokenPressure(BaseModel):
    """Evaluation of context window pressure for proactive compaction."""

    model_config = ConfigDict(extra="forbid")

    current_tokens: int = Field(
        ...,
        ge=0,
        description="Measured prompt tokens across messages and system instructions.",
    )
    context_limit: int = Field(
        ...,
        ge=1,
        description="Upper token limit of the target model context window.",
    )
    threshold_ratio: float = Field(
        default=0.75,
        ge=0.1,
        le=0.99,
        description="Ratio of context limit triggering proactive compaction.",
    )
    is_under_pressure: bool = Field(
        ...,
        description="True if current_tokens >= context_limit * threshold_ratio.",
    )
    reclaimable_estimate: int = Field(
        default=0,
        ge=0,
        description="Estimated token count reclaimable via pruning.",
    )


class CompactionCheckpoint(BaseModel):
    """Durable checkpoint summarizing a shadowed range of conversation turns."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this compaction checkpoint.",
    )
    conversation_id: str = Field(
        ...,
        description="Identifier of the conversation being compacted.",
    )
    turn_start: int = Field(
        ...,
        ge=0,
        description="Start index of the compacted turn range (inclusive).",
    )
    turn_end: int = Field(
        ...,
        ge=0,
        description="End index of the compacted turn range (inclusive).",
    )
    summary: str = Field(
        ...,
        min_length=1,
        description="Synthesized summary preserving core context across the range.",
    )
    shadowed_messages_count: int = Field(
        ...,
        ge=0,
        description="Number of original messages replaced by this checkpoint.",
    )
    tokens_reclaimed: int = Field(
        default=0,
        ge=0,
        description="Total tokens freed by this compaction.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the checkpoint was generated.",
    )
