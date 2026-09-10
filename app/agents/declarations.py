"""First-party production specialist declarations (spec P12/P13).

Static, immutable :class:`AgentDefinition` records for the three domain
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
KNOWLEDGE_RESEARCH_AGENT_NAME = "KnowledgeResearchAgent"

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
    capabilities=["calendar.*", "contacts.resolve_person"],
    allowed_tool_categories=["calendar", "contacts"],
    default_execution_mode=ExecutionMode.BOUNDED_REACT,
    delegation_allowed=True,
    max_child_depth=3,
)

KNOWLEDGE_RESEARCH_AGENT = AgentDefinition(
    name=KNOWLEDGE_RESEARCH_AGENT_NAME,
    description=(
        "Evidence-based research over internal RAG documents, Google Drive "
        "files, and external web sources with citations and injection immunity."
    ),
    domain=Domain.KNOWLEDGE_RESEARCH,
    # NOTE: "drive.list_folder" shares the drive.read label and is therefore
    # visible too; it is read-only and harmless (spec P13 §4.1). Drive mutation
    # tools share NO label with the patterns below, so they are invisible even
    # in the full (non-read-only) view — the gate strips what little remains.
    capabilities=[
        "retrieval.*",
        "drive.read",
        "drive.search",
        "drive.download",
        "web.search",
    ],
    allowed_tool_categories=["retrieval", "drive", "web"],
    default_execution_mode=ExecutionMode.BOUNDED_REACT,
    delegation_allowed=True,
    max_child_depth=3,
)

FIRST_PARTY_AGENTS: tuple[AgentDefinition, ...] = (
    COMMUNICATION_AGENT,
    CALENDAR_AGENT,
    KNOWLEDGE_RESEARCH_AGENT,
)


def build_first_party_registry() -> AgentRegistry:
    """Return a registry preloaded with the production declarations."""
    return AgentRegistry(FIRST_PARTY_AGENTS)


__all__ = [
    "CALENDAR_AGENT",
    "CALENDAR_AGENT_NAME",
    "COMMUNICATION_AGENT",
    "COMMUNICATION_AGENT_NAME",
    "FIRST_PARTY_AGENTS",
    "KNOWLEDGE_RESEARCH_AGENT",
    "KNOWLEDGE_RESEARCH_AGENT_NAME",
    "build_first_party_registry",
]
