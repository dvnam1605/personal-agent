"""Unit tests for P4 agent capability declarations and registry."""

import pytest

from app.agents import AgentRegistry
from app.domain.enums import Domain, ExecutionMode
from app.domain.errors import ConfigurationError
from app.domain.models import AgentDefinition


def _agent(name: str = "ResearchAgent") -> AgentDefinition:
    return AgentDefinition(
        name=name,
        description="Research-only test double",
        domain=Domain.KNOWLEDGE_RESEARCH,
        capabilities=["knowledge.read", "drive.read"],
        allowed_tool_categories=["knowledge", "drive"],
        default_execution_mode=ExecutionMode.BOUNDED_REACT,
    )


def test_duplicate_agent_registration_is_rejected() -> None:
    registry = AgentRegistry()
    registry.register(_agent())

    with pytest.raises(ConfigurationError, match="already registered"):
        registry.register(_agent())


def test_agent_capabilities_and_future_mock_agent_are_registry_data_only() -> None:
    registry = AgentRegistry([_agent()])
    registry.register(
        AgentDefinition(
            name="TravelAgent",
            description="Future test double only",
            domain=Domain.GENERAL,
            capabilities=["travel.read"],
            allowed_tool_categories=["travel"],
        )
    )

    assert registry.list_capabilities("ResearchAgent") == ["knowledge.read", "drive.read"]
    assert registry.list_capabilities()["TravelAgent"] == ["travel.read"]


def test_agent_registry_returns_defensive_copies() -> None:
    registry = AgentRegistry([_agent()])

    returned = registry.get("ResearchAgent")
    returned.capabilities.append("untrusted.write")

    assert registry.get("ResearchAgent").capabilities == ["knowledge.read", "drive.read"]
