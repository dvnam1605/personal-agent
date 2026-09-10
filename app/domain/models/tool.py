"""Tool contracts for tool registration, invocation, and audit metadata."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.models.agent import DelegationContext


class ToolDefinition(BaseModel):
    """Schema and capability contract for a registered tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        ...,
        description="Unique identifier for the tool (e.g. 'gmail.list_messages', 'calendar.create_event').",
    )
    description: str = Field(
        ...,
        description="Clear human/LLM-readable description of the tool purpose and behavior.",
    )
    category: str | None = Field(
        default=None,
        description="Tool family exposed to capability gates (for example 'gmail' or 'calendar').",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Fine-grained capabilities provided by this tool.",
    )
    parameters_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema dictionary defining input parameter types and constraints.",
    )
    risk_level: ActionRiskLevel = Field(
        default=ActionRiskLevel.READ_ONLY,
        description="Security and impact risk classification.",
    )
    is_mutation: bool = Field(
        default=False,
        description="Whether this tool performs state mutations (writes/deletes/sends).",
    )
    action_class: ActionClass | None = Field(
        default=None,
        description=(
            "Canonical policy class. Registered mutation tools must provide a non-READ class; "
            "read-only tools are normalized to READ by ToolRegistry."
        ),
    )
    required_scopes: list[str] = Field(
        default_factory=list,
        description="External OAuth scopes or permissions required to execute this tool.",
    )

    @field_validator("name", "description", mode="after")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        """Canonicalize required text so registry identity is deterministic."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tool name and description cannot be blank.")
        return normalized

    @field_validator("category", mode="after")
    @classmethod
    def normalize_category(cls, value: str | None) -> str | None:
        """Reject an explicitly blank category instead of silently falling back."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tool category cannot be blank when provided.")
        return normalized

    @field_validator("capabilities", mode="after")
    @classmethod
    def normalize_capabilities(cls, values: list[str]) -> list[str]:
        """Canonicalize fine-grained labels and reject blank entries."""
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("Tool capability values cannot be blank.")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def validate_mutation_risk_consistency(self) -> "ToolDefinition":
        """Ensure risk_level and is_mutation flag are logically consistent."""
        if self.is_mutation and self.risk_level == ActionRiskLevel.READ_ONLY:
            raise ValueError(
                f"Tool '{self.name}' is marked as is_mutation=True but has risk_level=READ_ONLY."
            )
        if not self.is_mutation and self.risk_level in (
            ActionRiskLevel.LOW_IMPACT_WRITE,
            ActionRiskLevel.HIGH_IMPACT_WRITE,
            ActionRiskLevel.IRREVERSIBLE,
        ):
            raise ValueError(
                f"Tool '{self.name}' has mutation risk level '{self.risk_level}' "
                f"but is marked as is_mutation=False."
            )
        if self.action_class is not None:
            if self.is_mutation and self.action_class == ActionClass.READ:
                raise ValueError(
                    f"Tool '{self.name}' is marked as a mutation but has action_class=READ."
                )
            if not self.is_mutation and self.action_class != ActionClass.READ:
                raise ValueError(
                    f"Tool '{self.name}' is read-only but has action_class='{self.action_class}'."
                )
        return self

    @property
    def tool_category(self) -> str:
        """Return the explicit category or the namespace in the tool name."""
        if self.category and self.category.strip():
            return self.category.strip()
        return self.name.split(".", 1)[0]

    @property
    def capability_names(self) -> tuple[str, ...]:
        """Return names used by registries when matching a capability request."""
        values = [self.name, self.tool_category, *self.capabilities]
        return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


class ToolInput(BaseModel):
    """Payload provided to execute a tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(
        ...,
        description="Target tool to invoke.",
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Invocation arguments mapped to the tool parameter schema.",
    )


class ToolContext(BaseModel):
    """Runtime context passed along with a tool invocation for authorization and tracing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(
        ...,
        description="Unique run ID of the current assistant execution.",
    )
    user_id: str = Field(
        ...,
        description="Identity of the active user executing the request.",
    )
    correlation_id: str | None = Field(
        default=None,
        description="Distributed tracing correlation ID.",
    )
    agent_name: str | None = Field(
        default=None,
        description="Name of the calling agent invoking the tool.",
    )
    read_only_view: bool = Field(
        default=False,
        description="Whether the current execution is strictly restricted to read-only tools.",
    )
    approval_token: str | None = Field(
        default=None,
        description="Validated approval token or approval_id authorizing high-risk mutation.",
    )
    delegation: DelegationContext | None = Field(
        default=None,
        description="Pinned delegation scope; approval_policy=NEVER must reject mutations.",
    )


class ToolExecutionMetadata(BaseModel):
    """Diagnostic and performance metadata for a completed tool call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(
        ...,
        description="Name of the executed tool.",
    )
    latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Tool execution wall-clock time in milliseconds.",
    )
    cached: bool = Field(
        default=False,
        description="Whether the result was served from an internal cache.",
    )
    retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of retries executed before final result.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of completion.",
    )

    @field_validator("timestamp", mode="after")
    @classmethod
    def ensure_utc_aware(cls, v: datetime) -> datetime:
        """Enforce UTC-aware timestamp."""
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("Timestamp must be UTC-aware (e.g. datetime.now(UTC)).")
        return v


class ToolResult(BaseModel):
    """Normalized result returned by any tool execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(
        ...,
        description="Name of the invoked tool.",
    )
    success: bool = Field(
        ...,
        description="Whether the tool execution succeeded.",
    )
    output: Any | None = Field(
        default=None,
        description="Structured output data if execution succeeded.",
    )
    error: str | None = Field(
        default=None,
        description="Error message if execution failed.",
    )
    metadata: ToolExecutionMetadata = Field(
        ...,
        description="Observability and timing metadata.",
    )

    @model_validator(mode="after")
    def validate_audit_and_outcome_integrity(self) -> "ToolResult":
        """Ensure metadata matches top-level tool identity and success/output/error are mutually consistent."""
        if self.tool_name != self.metadata.tool_name:
            raise ValueError(
                f"ToolResult tool_name '{self.tool_name}' must match metadata tool_name '{self.metadata.tool_name}'."
            )

        if self.success:
            if self.error is not None:
                raise ValueError(
                    f"ToolResult for '{self.tool_name}' has success=True but contains error: '{self.error}'."
                )
        else:
            if self.output is not None:
                raise ValueError(
                    f"ToolResult for '{self.tool_name}' has success=False but contains output data."
                )
            if self.error is None or not self.error.strip():
                raise ValueError(
                    f"ToolResult for '{self.tool_name}' has success=False and must contain an error message."
                )
        return self


class ToolRestriction(BaseModel):
    """Tool filtering restriction specifying allowed and/or denied tool patterns."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allow: list[str] | None = Field(
        default=None,
        description="Explicit allowlist of tool names or wildcard patterns.",
    )
    deny: list[str] | None = Field(
        default=None,
        description="Explicit denylist of tool names or wildcard patterns.",
    )

    @field_validator("allow", "deny", mode="after")
    @classmethod
    def normalize_filter_list(cls, values: list[str] | None) -> list[str] | None:
        """Normalize filter list strings and reject empty string items."""
        if values is None:
            return None
        normalized: list[str] = []
        for val in values:
            item = val.strip()
            if not item:
                raise ValueError("ToolRestriction pattern entries cannot be blank.")
            if item not in normalized:
                normalized.append(item)
        if not normalized:
            raise ValueError("ToolRestriction pattern list cannot be empty when provided.")
        return normalized

    @model_validator(mode="after")
    def validate_has_constraints(self) -> "ToolRestriction":
        """Ensure at least one of allow or deny is specified."""
        if self.allow is None and self.deny is None:
            raise ValueError("ToolRestriction must specify at least 'allow' or 'deny'.")
        return self
