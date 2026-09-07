"""First-party production specialist declarations (spec P12).

Static, immutable :class:`AgentDefinition` records for the two domain
specialists built on the P11 runtime. No business logic lives here: the
CapabilityGate consumes ``allowed_tool_categories`` / ``capabilities`` and
the SpecialistRunner consumes ``default_execution_mode``.
"""

from __future__ import annotations

from app.agents.registry import AgentRegistry
from app.domain.enums import Domain, ExecutionMode
from app.domain.models import AgentDefinition

COMMUNICATION_AGENT_NAME = "CommunicationAgent"
CALENDAR_AGENT_NAME = "CalendarAgent"

COMMUNICATION_AGENT = AgentDefinition(
    name=COMMUNICATION_AGENT_NAME,
    description=(
        "Email triage, thread summarization, contact resolution, and draft "
        "composition over Gmail and Google Contacts."
    ),
    domain=Domain.COMMUNICATION,
    capabilities=["gmail.*", "contacts.*"],
    allowed_tool_categories=["gmail", "contacts"],
    default_execution_mode=ExecutionMode.BOUNDED_REACT,
    delegation_allowed=True,
    max_child_depth=3,
)

CALENDAR_AGENT = AgentDefinition(
    name=CALENDAR_AGENT_NAME,
    description=(
        "Schedule querying, conflict detection, and deterministic multi-attendee "
        "free-slot calculation over Google Calendar."
    ),
    domain=Domain.CALENDAR,
    capabilities=["calendar.*"],
    allowed_tool_categories=["calendar"],
    default_execution_mode=ExecutionMode.BOUNDED_REACT,
    delegation_allowed=True,
    max_child_depth=3,
)

FIRST_PARTY_AGENTS: tuple[AgentDefinition, ...] = (
    COMMUNICATION_AGENT,
    CALENDAR_AGENT,
)


def build_first_party_registry() -> AgentRegistry:
    """Return a registry preloaded with the P12 production declarations."""
    return AgentRegistry(FIRST_PARTY_AGENTS)


__all__ = [
    "CALENDAR_AGENT",
    "CALENDAR_AGENT_NAME",
    "COMMUNICATION_AGENT",
    "COMMUNICATION_AGENT_NAME",
    "FIRST_PARTY_AGENTS",
    "build_first_party_registry",
]
