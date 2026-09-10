"""Unit tests for P4 capability gating and read-only exposure."""

import pytest

from app.agents import AgentRegistry
from app.domain.enums import ActionClass, ActionRiskLevel, Domain
from app.domain.errors import NotFoundError
from app.domain.models import AgentDefinition, ToolDefinition
from app.services import CapabilityGate
from app.tools import ToolRegistry


def _tool(
    name: str,
    *,
    category: str,
    capabilities: list[str],
    is_mutation: bool = False,
    action_class: ActionClass | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        category=category,
        capabilities=capabilities,
        risk_level=(
            ActionRiskLevel.HIGH_IMPACT_WRITE if is_mutation else ActionRiskLevel.READ_ONLY
        ),
        is_mutation=is_mutation,
        action_class=action_class,
    )


def test_agents_receive_only_declared_tool_categories() -> None:
    tools = ToolRegistry(
        [
            _tool("gmail.search", category="gmail", capabilities=["gmail.read"]),
            _tool("calendar.list", category="calendar", capabilities=["calendar.read"]),
            _tool("drive.search", category="drive", capabilities=["drive.read"]),
        ]
    )
    agents = AgentRegistry(
        [
            AgentDefinition(
                name="CommunicationAgent",
                description="Communication mock",
                domain=Domain.COMMUNICATION,
                capabilities=["gmail.read"],
                allowed_tool_categories=["gmail"],
            )
        ]
    )

    view = CapabilityGate(tools, agents).for_agent("CommunicationAgent")
    assert view.tool_names == ("gmail.search",)
    assert view.read_only is False


def test_explicit_tool_category_cannot_be_bypassed_by_name_namespace() -> None:
    tools = ToolRegistry(
        [
            _tool(
                "drive.delete",
                category="admin",
                capabilities=["drive.delete"],
                is_mutation=True,
                action_class=ActionClass.DESTRUCTIVE,
            )
        ]
    )
    agents = AgentRegistry(
        [
            AgentDefinition(
                name="DriveAgent",
                description="Drive-only mock",
                domain=Domain.KNOWLEDGE_RESEARCH,
                capabilities=["drive.delete"],
                allowed_tool_categories=["drive"],
            )
        ]
    )

    view = CapabilityGate(tools, agents).for_agent("DriveAgent")

    assert view.tool_names == ()


def test_research_read_only_mock_cannot_retrieve_mutation_by_name() -> None:
    tools = ToolRegistry(
        [
            _tool("knowledge.search", category="knowledge", capabilities=["knowledge.read"]),
            _tool(
                "drive.delete",
                category="drive",
                capabilities=["drive.delete"],
                is_mutation=True,
                action_class=ActionClass.DESTRUCTIVE,
            ),
        ]
    )
    agents = AgentRegistry(
        [
            AgentDefinition(
                name="ResearchAgent",
                description="Read-only research mock",
                domain=Domain.KNOWLEDGE_RESEARCH,
                capabilities=["knowledge.read", "drive.read"],
                allowed_tool_categories=["knowledge", "drive"],
            )
        ]
    )

    view = CapabilityGate(tools, agents).read_only_view("ResearchAgent")
    assert view.tool_names == ("knowledge.search",)
    with pytest.raises(NotFoundError):
        view.get("drive.delete")


def test_request_capabilities_can_narrow_but_not_widen_agent_view() -> None:
    tools = ToolRegistry(
        [
            _tool("gmail.search", category="gmail", capabilities=["gmail.read"]),
            _tool("gmail.labels", category="gmail", capabilities=["gmail.labels"]),
            _tool("calendar.list", category="calendar", capabilities=["calendar.read"]),
        ]
    )
    agents = AgentRegistry(
        [
            AgentDefinition(
                name="CommunicationAgent",
                description="Communication mock",
                domain=Domain.COMMUNICATION,
                capabilities=["gmail.read", "gmail.labels"],
                allowed_tool_categories=["gmail"],
            )
        ]
    )
    gate = CapabilityGate(tools, agents)

    narrowed = gate.for_agent(
        "CommunicationAgent",
        requested_capabilities=["gmail.read"],
    )
    attempted_widen = gate.for_agent(
        "CommunicationAgent",
        requested_capabilities=["calendar.read"],
    )

    assert narrowed.tool_names == ("gmail.search",)
    assert attempted_widen.tool_names == ()
