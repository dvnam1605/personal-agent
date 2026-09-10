"""Specialist runtime contracts (spec P11).

Pure Pydantic models shared by the ReAct runner, delegation service, and the
LangGraph harness boundary. No framework imports: the harness converts these
to/from graph channels without handing ``AssistantState`` to ``StateGraph``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ExecutionMode, SpecialistStatus, StopReason
from app.domain.models.agent import DelegationContext
from app.domain.models.budget import BudgetUsage, ExecutionBudget
from app.domain.models.tool import ToolRestriction

MessageRole = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    """One message in the specialist conversation sent to the LLM backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: MessageRole
    content: str = Field(min_length=1)


class ToolCallRequest(BaseModel):
    """A single tool invocation requested by the LLM backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = Field(default=None)


class AssistantTurn(BaseModel):
    """One LLM backend response: text plus zero or more tool calls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(default="")
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    prompt_tokens: int = Field(
        default=0,
        ge=0,
        description="Prompt tokens consumed by this turn (0 when unreported).",
    )
    completion_tokens: int = Field(
        default=0,
        ge=0,
        description="Completion tokens consumed by this turn (0 when unreported).",
    )


class SpecialistReport(BaseModel):
    """Structured conclusion every specialist must return (spec P11-10)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SpecialistStatus
    summary: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] | None = Field(default=None)
    missing_context: list[str] | None = Field(default=None)


class ReActTraceStep(BaseModel):
    """One recorded ReAct iteration (spec P11-05). Never stores chain-of-thought."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    iteration: int = Field(ge=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    observation_summary: str = Field(default="")
    success: bool = Field(default=True)
    reminder_injected: bool = Field(default=False)


class SpecialistTrace(BaseModel):
    """Ordered trace steps plus the terminal stop reason (spec P11-05)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    steps: list[ReActTraceStep] = Field(default_factory=list)
    stop_reason: StopReason = StopReason.SUCCESS
    escalated_to_react: bool = Field(default=False)
    circuit_broken: bool = Field(default=False)


class SpecialistTask(BaseModel):
    """Everything one specialist activation needs (spec P11-01/02/06/07)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    mode: ExecutionMode | None = Field(
        default=None,
        description="Explicit mode wins; otherwise the agent declaration default applies.",
    )
    context_data: dict[str, Any] = Field(default_factory=dict)
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    tool_restriction: ToolRestriction | None = Field(default=None)
    permit_mutations: bool = Field(
        default=False,
        description="Fail-closed by default: approval-requiring mutation ops are rejected unless explicitly authorized.",
    )
    approval_token: str | None = Field(
        default=None,
        description="Optional security token granting authorization for permitted mutation tools.",
    )
    approval_policy: Literal["NEVER", "ALWAYS", "POLICY"] = Field(
        default="POLICY",
        description="Delegation policy pinning (§17.1): child specialists cannot self-approve (NEVER when delegated).",
    )
    sandbox_scope: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Frozen sandbox scope boundary captured before first await (§17.1).",
    )
    delegation: DelegationContext | None = Field(default=None)
    system_preamble: tuple[str, ...] = Field(default_factory=tuple)


class SpecialistOutcome(BaseModel):
    """Terminal result of one specialist activation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: str = Field(min_length=1)
    report: SpecialistReport
    trace: SpecialistTrace = Field(default_factory=SpecialistTrace)
    usage: BudgetUsage = Field(default_factory=BudgetUsage)
    needs_approval: bool = Field(default=False)
    delegation_depth: int = Field(default=0, ge=0)


class DelegationRequest(BaseModel):
    """Input to the single delegation entry point (spec P11-07)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_agent: str = Field(min_length=1)
    parent_depth: int = Field(default=0, ge=0)
    target_agent: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    context_data: dict[str, Any] = Field(default_factory=dict)
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    tool_restriction: ToolRestriction | None = Field(default=None)
    system_preamble: tuple[str, ...] = Field(default_factory=tuple)
