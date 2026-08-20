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


def test_agent_delegation_scope_metadata_validation() -> None:
    agent = AgentDefinition(
        name="DelegatingAgent",
        description="Agent with delegation permissions",
        domain=Domain.GENERAL,
        capabilities=["task.manage"],
        allowed_tool_categories=["tasks"],
        delegation_allowed=True,
        max_child_depth=2,
        inherits_parent_tools=True,
    )
    assert agent.delegation_allowed is True
    assert agent.max_child_depth == 2
    assert agent.inherits_parent_tools is True

    # Negative child depth is rejected
    with pytest.raises(ValueError, match="max_child_depth"):
        AgentDefinition(
            name="InvalidAgent",
            description="Agent with invalid depth",
            domain=Domain.SYSTEM,
            max_child_depth=-1,
        )

