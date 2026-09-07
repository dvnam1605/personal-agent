"""Unit tests for P12 first-party agent declarations and gating (P12 entry)."""

from app.agents import (
    CALENDAR_AGENT_NAME,
    COMMUNICATION_AGENT_NAME,
    build_first_party_registry,
)
from app.domain.enums import Domain, ExecutionMode
from app.services import CapabilityGate
from app.tools import (
    CALENDAR_TOOL_DEFINITIONS,
    COMMUNICATION_TOOL_DEFINITIONS,
    ToolRegistry,
)


def _gate() -> CapabilityGate:
    tools = ToolRegistry([*COMMUNICATION_TOOL_DEFINITIONS, *CALENDAR_TOOL_DEFINITIONS])
    return CapabilityGate(tools, build_first_party_registry())


def test_first_party_registry_declares_both_domain_agents() -> None:
    registry = build_first_party_registry()
    communication = registry.get(COMMUNICATION_AGENT_NAME)
    calendar = registry.get(CALENDAR_AGENT_NAME)

    assert communication.domain is Domain.COMMUNICATION
    assert communication.allowed_tool_categories == ["gmail", "contacts"]
    assert communication.default_execution_mode is ExecutionMode.BOUNDED_REACT
    assert communication.delegation_allowed is True

    assert calendar.domain is Domain.CALENDAR
    assert calendar.allowed_tool_categories == ["calendar"]
    assert calendar.default_execution_mode is ExecutionMode.BOUNDED_REACT
    assert calendar.delegation_allowed is True


def test_communication_agent_sees_only_gmail_and_contacts() -> None:
    view = _gate().for_agent(COMMUNICATION_AGENT_NAME)

    assert len(view.tool_names) > 0
    assert all(
        name.startswith("gmail.") or name.startswith("contacts.") for name in view.tool_names
    )
    assert "gmail.send_draft" in view.tool_names
    assert "contacts.resolve_person" in view.tool_names
    assert not any(name.startswith("calendar.") for name in view.tool_names)


def test_calendar_agent_sees_only_calendar() -> None:
    view = _gate().for_agent(CALENDAR_AGENT_NAME)

    assert len(view.tool_names) > 0
    assert all(name.startswith("calendar.") for name in view.tool_names)
    assert "calendar.find_free_slots" in view.tool_names
    assert "calendar.create_event" in view.tool_names
    assert not any(name.startswith("gmail.") for name in view.tool_names)


def test_read_only_views_hide_every_mutation_tool() -> None:
    gate = _gate()
    for agent_name in (COMMUNICATION_AGENT_NAME, CALENDAR_AGENT_NAME):
        full = gate.for_agent(agent_name)
        read_only = gate.read_only_view(agent_name)

        assert any(tool.is_mutation for tool in full.list()), (
            f"{agent_name} full view should include gated mutations"
        )
        assert read_only.read_only is True
        assert all(not tool.is_mutation for tool in read_only.list())
