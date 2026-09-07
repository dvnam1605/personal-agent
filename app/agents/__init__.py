"""Agent declarations and registry package."""

from app.agents.declarations import (
    CALENDAR_AGENT,
    CALENDAR_AGENT_NAME,
    COMMUNICATION_AGENT,
    COMMUNICATION_AGENT_NAME,
    FIRST_PARTY_AGENTS,
    build_first_party_registry,
)
from app.agents.registry import AgentRegistry
from app.domain.models import AgentDefinition

__all__ = [
    "CALENDAR_AGENT",
    "CALENDAR_AGENT_NAME",
    "COMMUNICATION_AGENT",
    "COMMUNICATION_AGENT_NAME",
    "FIRST_PARTY_AGENTS",
    "AgentDefinition",
    "AgentRegistry",
    "build_first_party_registry",
]
