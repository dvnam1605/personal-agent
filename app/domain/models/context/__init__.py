"""Domain models for assembled context slices (spec P17-06)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.context.entity import EntityRecord, EntityResolutionResult
from app.domain.models.context.memory import MemoryItem


class ContextSlice(BaseModel):
    """Bundled context injected into specialist prompts or execution state."""

    model_config = ConfigDict(extra="forbid")

    resolved_entities: list[EntityRecord] = Field(
        default_factory=list,
        description="Entities resolved from the user query or conversation history.",
    )
    ambiguous_entities: list[EntityResolutionResult] = Field(
        default_factory=list,
        description="Ambiguous entity references requiring user clarification.",
    )
    preferences: list[MemoryItem] = Field(
        default_factory=list,
        description="Active, non-stale user preferences relevant to this run.",
    )
    episodic_facts: list[MemoryItem] = Field(
        default_factory=list,
        description="Confirmed episodic memories and decisions from past events.",
    )
    working_memory: dict[str, Any] = Field(
        default_factory=dict,
        description="Ephemeral working memory parameters for the active task.",
    )
    rendered_prompt_section: str = Field(
        default="",
        description="Formatted text block ready for prompt preambles.",
    )

    def is_empty(self) -> bool:
        """True if the slice carries no entities, preferences, or facts."""
        return (
            not self.resolved_entities
            and not self.ambiguous_entities
            and not self.preferences
            and not self.episodic_facts
            and not self.working_memory
            and not self.rendered_prompt_section.strip()
        )
