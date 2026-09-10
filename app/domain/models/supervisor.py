"""Supervisor domain models and capability contracts (spec P16 / ADR 0011).

Pure Pydantic models for Supervisor planning, capability discovery, and
subagent session continuation. Zero framework imports.
"""

from __future__ import annotations

import uuid
from fnmatch import fnmatchcase
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Domain
from app.domain.models.evidence import EvidenceItem
from app.domain.models.specialist import ChatMessage
from app.domain.models.tool import ToolRestriction


def _capability_matches(pattern: str, candidate: str) -> bool:
    """Same glob/prefix rules as ``app.tools.registry.capability_matches`` (stdlib only)."""
    if not pattern or not candidate:
        return False
    if pattern == "*":
        return True
    return (
        pattern == candidate
        or candidate.startswith(f"{pattern}.")
        or (pattern.endswith(".*") and candidate == pattern[:-2])
        or fnmatchcase(candidate, pattern)
    )


class AgentCapabilityDescriptor(BaseModel):
    """High-level capability view of one specialist agent for the Supervisor planner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: str = Field(
        ...,
        min_length=1,
        description="Identifier of the specialist agent.",
    )
    domain: Domain = Field(
        ...,
        description="Business domain owned by the specialist.",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="High-level description of what this agent can do.",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Named capabilities supported by this agent (no low-level tools).",
    )
    max_child_depth: int | None = Field(
        default=3,
        description="Maximum delegation depth supported by this agent.",
    )


class CapabilityCatalog(BaseModel):
    """Read-only catalog of all active agent capabilities presented to the Supervisor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: list[AgentCapabilityDescriptor] = Field(
        default_factory=list,
        description="List of registered agent capability descriptors.",
    )

    def get_agent(self, name: str) -> AgentCapabilityDescriptor | None:
        """Find an agent descriptor by exact name."""
        for ag in self.agents:
            if ag.agent_name == name:
                return ag
        return None

    def get_agent_for_capability(self, capability: str) -> str | None:
        """Find an agent name that provides the requested capability (glob-aware)."""
        normalized = capability.strip().lower()
        if not normalized:
            return None
        for ag in self.agents:
            for cap in ag.capabilities:
                declared = cap.strip().lower()
                if not declared:
                    continue
                if _capability_matches(declared, normalized) or _capability_matches(
                    normalized, declared
                ):
                    return ag.agent_name
        return None

    @property
    def all_capabilities(self) -> list[str]:
        """List all unique capabilities across all registered agents."""
        caps: list[str] = []
        for ag in self.agents:
            for cap in ag.capabilities:
                if cap not in caps:
                    caps.append(cap)
        return caps

    def format_prompt_catalog(self) -> str:
        """Format the capability catalog as structured text for the planner prompt."""
        lines: list[str] = ["Available Specialists and Capabilities:"]
        for ag in self.agents:
            caps_str = ", ".join(ag.capabilities) if ag.capabilities else "(none)"
            lines.append(f"- **{ag.agent_name}** (Domain: {ag.domain.value}): {ag.description}")
            lines.append(f"  Capabilities: {caps_str}")
        return "\n".join(lines)


class SubagentSessionState(BaseModel):
    """Conversation and context state for a continuable subagent session (P16-05C)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    session_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique session identifier for the continuable subagent.",
    )
    agent_name: str = Field(
        ...,
        description="Name of the specialist running in this session.",
    )
    user_id: str = Field(
        ...,
        description="Tenant user ID owning this session.",
    )
    parent_run_id: str = Field(
        ...,
        description="Parent assistant run ID.",
    )
    messages: list[ChatMessage] = Field(
        default_factory=list,
        description="Message history for multi-turn continuations.",
    )
    context_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Accumulated session context data.",
    )
    depth: int = Field(
        default=0,
        ge=0,
        description="Delegation depth of this subagent.",
    )
    tool_restriction: ToolRestriction | None = Field(
        default=None,
        description="Scoped tool filter applied to this session.",
    )
    is_active: bool = Field(
        default=True,
        description="Whether this session is active and accepting new turns.",
    )
    turn_count: int = Field(
        default=0,
        ge=0,
        description="Number of conversation turns completed in this session.",
    )


class SupervisorResult(BaseModel):
    """Grounded outcome of a Supervisor DAG execution plan (P16-08)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(..., description="ID of the executed plan.")
    goal: str = Field(..., description="Goal the supervisor executed.")
    status: str = Field(
        ..., description="Final outcome status (completed, failed, blocked, needs_approval)."
    )
    tasks_total: int = Field(default=0, ge=0)
    tasks_completed: int = Field(default=0, ge=0)
    task_results: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    needs_approval: list[dict[str, Any]] = Field(default_factory=list)
    synthesis: str = Field(default="", description="Final synthesized answer or report.")
    replan_count: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)
