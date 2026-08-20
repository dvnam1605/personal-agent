"""Domain models for tool output spill storage, preview retention, and locators."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SpillRef(BaseModel):
    """Metadata reference to an oversized tool output persisted in a spill store."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    locator: str = Field(
        ...,
        description="Model-facing opaque URI or path handle (e.g. 'spill://session/artifact_id' or filesystem path).",
    )
    byte_count: int = Field(
        ...,
        ge=0,
        description="Exact UTF-8 byte count of the persisted payload.",
    )
    character_count: int = Field(
        ...,
        ge=0,
        description="Character count of the persisted string.",
    )
    retrieval_hint: str = Field(
        ...,
        description="Guidance for the agent on how to retrieve or inspect slices of the spilled content.",
    )
    session_id: str = Field(
        ...,
        description="Storage namespace / session ID that owns this spilled artifact.",
    )
    artifact_id: str = Field(
        ...,
        description="Unique artifact ID within the session.",
    )
    tool_name: str | None = Field(
        default=None,
        description="Name of the tool that produced the spilled output.",
    )
    call_id: str | None = Field(
        default=None,
        description="Optional tool call execution ID.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the spill was created.",
    )

    @field_validator("locator", "retrieval_hint", "session_id", "artifact_id", mode="after")
    @classmethod
    def normalize_non_blank_strings(cls, v: str) -> str:
        """Validate and normalize non-blank strings."""
        val = v.strip()
        if not val:
            raise ValueError("Field cannot be blank.")
        return val

    @field_validator("created_at", mode="after")
    @classmethod
    def ensure_utc_aware(cls, v: datetime) -> datetime:
        """Enforce UTC-aware timestamp."""
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("Timestamp must be UTC-aware.")
        return v


class SpillPolicyConfig(BaseModel):
    """Configuration governing when tool outputs are spilled and how previews are bounded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_inline_bytes: int = Field(
        default=4096,
        ge=64,
        description="Maximum allowed UTF-8 bytes for inline tool output before spilling to storage.",
    )
    head_bytes: int | None = Field(
        default=None,
        ge=0,
        description="Optional explicit head preview byte budget (defaults to max_inline_bytes // 2).",
    )
    tail_bytes: int | None = Field(
        default=None,
        ge=0,
        description="Optional explicit tail preview byte budget (defaults to max_inline_bytes // 2).",
    )
    enabled: bool = Field(
        default=True,
        description="Whether the spill policy is actively applied to tool executions.",
    )
    excluded_tools: list[str] = Field(
        default_factory=lambda: ["spill.fetch", "spill.slice", "spill.info", "read"],
        description="Tool names excluded from automatic spilling to prevent retrieval loops.",
    )

    @model_validator(mode="after")
    def validate_budget_split(self) -> "SpillPolicyConfig":
        """Validate head and tail budgets if provided."""
        if self.head_bytes is not None and self.tail_bytes is not None:
            if self.head_bytes + self.tail_bytes > self.max_inline_bytes:
                raise ValueError("head_bytes + tail_bytes cannot exceed max_inline_bytes.")
        return self


class SpilledOutput(BaseModel):
    """Structured representation of a bounded preview replacing an oversized tool result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    is_spilled: bool = Field(
        default=True,
        description="Always True to denote this output was spilled to external storage.",
    )
    preview: str = Field(
        ...,
        description="Bounded head/tail preview of the original tool output.",
    )
    locator: str = Field(
        ...,
        description="Locator handle where the full result can be retrieved.",
    )
    original_bytes: int = Field(
        ...,
        ge=0,
        description="Total UTF-8 bytes of the full uncompressed result.",
    )
    omitted_bytes: int = Field(
        ...,
        ge=0,
        description="Number of bytes omitted from the inline response.",
    )
    retrieval_hint: str = Field(
        ...,
        description="Guidance on retrieving full or sliced content.",
    )
    notice: str = Field(
        ...,
        description="Full formatted omission notice string.",
    )
    formatted_content: str = Field(
        ...,
        description="Combined preview + notice suitable for direct model consumption.",
    )
