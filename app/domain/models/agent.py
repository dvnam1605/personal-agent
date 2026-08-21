"""Agent contracts for multi-agent coordination and task delegation."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import Domain, ExecutionMode, TaskStatus
from app.domain.models.action import ProposedAction
from app.domain.models.budget import ExecutionBudget
from app.domain.models.evidence import EvidenceItem


class NeedMoreContext(BaseModel):
    """Signal when an agent requires additional clarification or missing information."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(
        ...,
        description="Clarifying question to prompt the user or supervisor.",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="List of specific missing entities or parameters.",
    )
    suggested_source: str | None = Field(
        default=None,
        description="Suggested subsystem or agent to consult for the missing data.",
    )


class CapabilityRequest(BaseModel):
    """Delegation or escalation request when an agent lacks a required tool/capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_capability: str = Field(
        ...,
        description="Name of the capability or tool needed.",
    )
    target_agent: str = Field(
        ...,
        description="Suggested specialist agent capable of performing the operation.",
    )
    reason: str = Field(
        ...,
        description="Rationale why this capability is required to achieve the goal.",
    )


class AgentDefinition(BaseModel):
    """Static capability declaration used by AgentRegistry and CapabilityGate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        ...,
        min_length=1,
        description="Stable registry identifier for the agent role.",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="Human-readable description of the agent's responsibility.",
    )
    domain: Domain = Field(
        ...,
        description="Business domain owned by the agent.",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Functional capabilities the agent may advertise or request.",
    )
    allowed_tool_categories: list[str] = Field(
        default_factory=list,
        description="Tool namespaces the CapabilityGate may expose to this agent.",
    )
    default_execution_mode: ExecutionMode = Field(
        default=ExecutionMode.DIRECT,
        description="Default specialist execution mode for this agent role.",
    )
    delegation_allowed: bool = Field(
        default=True,
        description="Whether this agent is permitted to delegate tasks to sub-agents.",
    )
    max_child_depth: int | None = Field(
        default=3,
        description="Maximum child delegation depth allowed for this agent.",
    )
    inherits_parent_tools: bool = Field(
        default=False,
        description="Whether delegated child executions inherit parent tool scopes.",
    )

    @field_validator("max_child_depth", mode="after")
    @classmethod
    def validate_child_depth(cls, value: int | None) -> int | None:
        """Ensure max_child_depth is non-negative when configured."""
        if value is not None and value < 0:
            raise ValueError("max_child_depth must be non-negative.")
        return value

    @field_validator("name", "description", mode="after")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Reject declarations that are technically non-empty but only whitespace."""
        if not value.strip():
            raise ValueError("Agent declaration text cannot be blank.")
        return value.strip()

    @field_validator("capabilities", "allowed_tool_categories", mode="after")
    @classmethod
    def normalize_capability_values(cls, values: list[str]) -> list[str]:
        """Normalize and de-duplicate capability patterns deterministically."""
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("Agent capability values cannot be blank.")
            if item not in normalized:
                normalized.append(item)
        return normalized


class DelegationContext(BaseModel):
    """Frozen execution scope and policy boundaries for delegated agent activation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_agent: str = Field(
        ...,
        min_length=1,
        description="Identifier of the delegating parent agent.",
    )
    target_agent: str = Field(
        ...,
        min_length=1,
        description="Identifier of the delegated child agent.",
    )
    delegation_depth: int = Field(
        default=0,
        ge=0,
        description="Current depth in the delegation hierarchy.",
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Restricted subset of tools explicitly permitted for the child.",
    )
    read_only: bool = Field(
        default=False,
        description="Whether the child is restricted strictly to read-only tools.",
    )
    policy_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Inherited or scoped policy constraints.",
    )

    @field_validator("parent_agent", "target_agent", mode="after")
    @classmethod
    def reject_blank_agent_names(cls, value: str) -> str:
        """Reject blank agent identifiers in delegation context."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent names in DelegationContext cannot be blank.")
        return normalized


class TaskResult(BaseModel):
    """Result of an individual planned sub-task executed by an agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(
        ...,
        description="Identifier of the task in the execution plan.",
    )
    status: TaskStatus = Field(
        ...,
        description="Outcome status of the task.",
    )
    result_data: Any | None = Field(
        default=None,
        description="Payload or structured outcome produced by the task.",
    )
    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="Grounded evidence items gathered during task execution.",
    )
    error: str | None = Field(
        default=None,
        description="Error detail if the task failed.",
    )


class AgentRequest(BaseModel):
    """Payload dispatched to activate a specialist agent or supervisor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(
        ...,
        description="Unique run ID.",
    )
    agent_name: str = Field(
        ...,
        description="Identifier of the receiving agent.",
    )
    goal: str = Field(
        ...,
        description="Target goal or instruction for this activation.",
    )
    context_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Input context, parameters, and prior state necessary for execution.",
    )
    available_tools: list[str] = Field(
        default_factory=list,
        description="Explicit list of tool names this agent is gated to invoke.",
    )
    budget: ExecutionBudget = Field(
        default_factory=ExecutionBudget,
        description="Resource allocation and limits for this activation.",
    )


class AgentResult(BaseModel):
    """Normalized response returned by an agent upon completion of its turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: str = Field(
        ...,
        description="Identifier of the responding agent.",
    )
    success: bool = Field(
        ...,
        description="Whether the agent achieved its goal or completed successfully.",
    )
    output: str | None = Field(
        default=None,
        description="Synthesized text or final answer produced by the agent.",
    )
    task_results: list[TaskResult] = Field(
        default_factory=list,
        description="Individual results of sub-tasks performed during this turn.",
    )
    new_evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="New evidence records gathered during execution.",
    )
    need_more_context: NeedMoreContext | None = Field(
        default=None,
        description="Clarification request if execution cannot proceed.",
    )
    proposed_actions: list[ProposedAction] = Field(
        default_factory=list,
        description="Typed actions proposed that require policy review or human approval.",
    )
    capability_requests: list[CapabilityRequest] = Field(
        default_factory=list,
        description="Requests for capabilities outside this agent's boundary.",
    )


class DelegationResult(AgentResult):
    """Extended result for delegated execution turns, tracking approval and chain depth."""

    needs_approval: bool = Field(
        default=False,
        description="Whether delegated execution paused awaiting user/policy approval.",
    )
    delegation_depth: int = Field(
        default=0,
        ge=0,
        description="Delegation depth at which this result was produced.",
    )
