"""Unit tests for Workflow models, graph validation, and reachability."""

import pytest
from pydantic import ValidationError

from app.domain.models import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowExecutionResult,
    WorkflowNode,
)


def test_valid_multi_entry_workflow_definition() -> None:
    """Verify meeting prep workflow with parallel entry nodes converging into synthesis node."""
    n1 = WorkflowNode(
        id="node_fetch_emails",
        node_type="tool_call",
        assigned_agent="CommunicationAgent",
        action="gmail.list_messages",
    )
    n2 = WorkflowNode(
        id="node_fetch_calendar",
        node_type="tool_call",
        assigned_agent="CalendarAgent",
        action="calendar.get_free_busy",
    )
    n3 = WorkflowNode(
        id="node_synthesize",
        node_type="llm_synthesis",
        assigned_agent="SupervisorAgent",
        action="synthesize_meeting_brief",
    )
    edges = [
        WorkflowEdge(source_node_id="node_fetch_emails", target_node_id="node_synthesize"),
        WorkflowEdge(source_node_id="node_fetch_calendar", target_node_id="node_synthesize"),
    ]

    # Both node_fetch_emails and node_fetch_calendar are root entry nodes with in-degree 0
    wf = WorkflowDefinition(
        name="meeting_prep_graph",
        description="Parallel gathering of email & calendar context and final synthesis",
        entry_node_ids=["node_fetch_emails", "node_fetch_calendar"],
        nodes=[n1, n2, n3],
        edges=edges,
    )

    assert wf.name == "meeting_prep_graph"
    assert len(wf.nodes) == 3
    assert len(wf.edges) == 2
    assert len(wf.entry_node_ids) == 2
    assert wf.entry_node_id == "node_fetch_emails"


def test_workflow_duplicate_entry_node_ids_rejected() -> None:
    """Verify duplicate entry_node_ids are rejected."""
    n1 = WorkflowNode(id="node_1", node_type="tool_call", action="a1")
    with pytest.raises(ValidationError, match="Duplicate entry_node_ids"):
        WorkflowDefinition(
            name="dup_entry_wf",
            description="dup entry",
            entry_node_ids=["node_1", "node_1"],
            nodes=[n1],
        )


def test_workflow_dependent_node_as_entry_rejected() -> None:
    """Verify entry node with incoming edges is rejected."""
    n1 = WorkflowNode(id="node_1", node_type="tool_call", action="a1")
    n2 = WorkflowNode(id="node_2", node_type="tool_call", action="a2")
    edges = [WorkflowEdge(source_node_id="node_1", target_node_id="node_2")]

    # node_2 has in-degree 1 from node_1, so it cannot be declared as an entry node
    with pytest.raises(ValidationError, match="cannot have incoming edges"):
        WorkflowDefinition(
            name="dependent_entry_wf",
            description="invalid entry",
            entry_node_ids=["node_1", "node_2"],
            nodes=[n1, n2],
            edges=edges,
        )


def test_workflow_undeclared_root_node_rejected() -> None:
    """Verify any node with 0 incoming edges must be declared in entry_node_ids."""
    n1 = WorkflowNode(id="node_1", node_type="tool_call", action="a1")
    n2 = WorkflowNode(id="node_2", node_type="tool_call", action="a2")
    n3 = WorkflowNode(id="node_3", node_type="tool_call", action="a3")
    edges = [
        WorkflowEdge(source_node_id="node_1", target_node_id="node_3"),
        WorkflowEdge(source_node_id="node_2", target_node_id="node_3"),
    ]

    # node_2 has in-degree 0 but is not in entry_node_ids
    with pytest.raises(ValidationError, match="must be declared in entry_node_ids"):
        WorkflowDefinition(
            name="undeclared_root_wf",
            description="missing root entry",
            entry_node_ids=["node_1"],
            nodes=[n1, n2, n3],
            edges=edges,
        )


def test_workflow_unreachable_node_rejected() -> None:
    """Verify disconnected/unreachable nodes are rejected by reachability validation."""
    n1 = WorkflowNode(id="node_1", node_type="tool_call", action="a1")
    n2 = WorkflowNode(id="node_2", node_type="tool_call", action="a2")
    n_island = WorkflowNode(id="island_node", node_type="tool_call", action="a3")

    edges = [
        WorkflowEdge(source_node_id="node_1", target_node_id="node_2"),
    ]

    # island_node has no edge and is not listed in entry_node_ids
    with pytest.raises(ValidationError, match="must be declared in entry_node_ids"):
        WorkflowDefinition(
            name="disconnected_wf",
            description="disconnected",
            entry_node_ids=["node_1"],
            nodes=[n1, n2, n_island],
            edges=edges,
        )


def test_workflow_duplicate_node_ids() -> None:
    """Verify duplicate node IDs are rejected."""
    n1 = WorkflowNode(id="dup", node_type="tool_call", action="a1")
    n2 = WorkflowNode(id="dup", node_type="tool_call", action="a2")
    with pytest.raises(ValidationError, match="Duplicate node IDs"):
        WorkflowDefinition(
            name="bad_wf",
            description="dup nodes",
            entry_node_ids=["dup"],
            nodes=[n1, n2],
        )


def test_workflow_missing_entry_node() -> None:
    """Verify non-existent entry_node_ids are rejected."""
    n1 = WorkflowNode(id="node_1", node_type="tool_call", action="a1")
    with pytest.raises(ValidationError, match="does not exist in workflow nodes"):
        WorkflowDefinition(
            name="bad_wf",
            description="missing entry",
            entry_node_ids=["non_existent"],
            nodes=[n1],
        )


def test_workflow_invalid_edge_endpoints() -> None:
    """Verify edge endpoints must reference real nodes."""
    n1 = WorkflowNode(id="node_1", node_type="tool_call", action="a1")
    with pytest.raises(ValidationError, match="Edge target 'node_2' does not exist"):
        WorkflowDefinition(
            name="bad_wf",
            description="missing edge target",
            entry_node_ids=["node_1"],
            nodes=[n1],
            edges=[WorkflowEdge(source_node_id="node_1", target_node_id="node_2")],
        )

    with pytest.raises(ValidationError, match="Edge source 'node_0' does not exist"):
        WorkflowDefinition(
            name="bad_wf",
            description="missing edge source",
            entry_node_ids=["node_1"],
            nodes=[n1],
            edges=[WorkflowEdge(source_node_id="node_0", target_node_id="node_1")],
        )


def test_workflow_self_loop_rejected() -> None:
    """Verify self-loop on node is rejected."""
    n1 = WorkflowNode(id="node_1", node_type="tool_call", action="a1")
    with pytest.raises(ValidationError, match="Self-loop detected"):
        WorkflowDefinition(
            name="bad_wf",
            description="self loop",
            entry_node_ids=["node_1"],
            nodes=[n1],
            edges=[WorkflowEdge(source_node_id="node_1", target_node_id="node_1")],
        )


def test_workflow_cycle_rejected() -> None:
    """Verify cycle detection in workflow edges."""
    n1 = WorkflowNode(id="node_1", node_type="tool_call", action="a1")
    n2 = WorkflowNode(id="node_2", node_type="tool_call", action="a2")
    edges = [
        WorkflowEdge(source_node_id="node_1", target_node_id="node_2"),
        WorkflowEdge(source_node_id="node_2", target_node_id="node_1"),
    ]
    with pytest.raises(ValidationError, match="cannot have incoming edges"):
        WorkflowDefinition(
            name="bad_wf",
            description="cyclic workflow",
            entry_node_ids=["node_1"],
            nodes=[n1, n2],
            edges=edges,
        )


def test_workflow_execution_result() -> None:
    """Verify WorkflowExecutionResult creation."""
    res = WorkflowExecutionResult(
        workflow_name="meeting_prep_graph",
        success=True,
        executed_node_ids=["node_fetch_emails", "node_fetch_calendar", "node_synthesize"],
        outputs={"summary": "Meeting scheduled for 3 PM"},
    )
    assert res.success is True
    assert len(res.executed_node_ids) == 3
    assert res.outputs["summary"] == "Meeting scheduled for 3 PM"
