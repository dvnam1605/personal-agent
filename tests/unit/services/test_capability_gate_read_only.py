"""Unit tests for CapabilityGate read-only enforcement (spec P13 §4.1 / H-NEW1)."""

from __future__ import annotations

from app.agents.registry import AgentRegistry
from app.services.capability_gate import CapabilityGate
from app.tools.registry import ToolRegistry
from tests.unit.agents import _fakes as fakes


def test_capability_gate_read_only_strips_mutation_tools() -> None:
    read_tool = fakes.make_read_tool("doc.read")
    write_tool = fakes.make_mutation_tool("doc.write")
    delete_tool = fakes.make_mutation_tool("doc.delete")

    tools = ToolRegistry([read_tool, write_tool, delete_tool])
    agent = fakes.make_agent("ResearchAgent", categories=["test"])
    agents = AgentRegistry([agent])
    gate = CapabilityGate(tools, agents)

    # Standard view includes write and delete
    standard_view = gate.for_agent("ResearchAgent", read_only=False)
    assert "doc.read" in standard_view.tool_names
    assert "doc.write" in standard_view.tool_names
    assert "doc.delete" in standard_view.tool_names
    assert standard_view.is_read_only is False

    # Read-only view strips all mutation tools
    ro_view = gate.for_agent("ResearchAgent", read_only=True)
    assert "doc.read" in ro_view.tool_names
    assert "doc.write" not in ro_view.tool_names
    assert "doc.delete" not in ro_view.tool_names
    assert ro_view.is_read_only is True

    # read_only_view alias
    ro_alias = gate.read_only_view("ResearchAgent")
    assert "doc.read" in ro_alias.tool_names
    assert "doc.write" not in ro_alias.tool_names
    assert "doc.delete" not in ro_alias.tool_names
    assert ro_alias.is_read_only is True

    # can_access checks
    assert gate.can_access("ResearchAgent", "doc.read", read_only=True) is True
    assert gate.can_access("ResearchAgent", "doc.write", read_only=True) is False
    assert gate.can_access("ResearchAgent", "doc.delete", read_only=True) is False
    assert gate.can_access("ResearchAgent", "doc.write", read_only=False) is True
