"""Unit tests for Agent communication contracts."""

import pytest

from app.domain.enums import Domain, EvidenceType, TaskStatus
from app.domain.models import (
    AgentDefinition,
    DelegationContext,
    DelegationResult,
    EvidenceItem,
    EvidenceSource,
    TaskResult,
)


def test_task_result_with_evidence() -> None:
    """Verify TaskResult with grounded evidence (consumed by AssistantState)."""
    source = EvidenceSource(source_type="calendar", source_id="evt_100")
    evidence = EvidenceItem(
        evidence_type=EvidenceType.CALENDAR_EVENT,
        content="Free slot available 14:00-15:00 Tuesday",
        source=source,
    )
    task_res = TaskResult(
        task_id="t1",
        status=TaskStatus.COMPLETED,
        result_data={"slot": "14:00-15:00"},
        evidence=[evidence],
    )

    assert task_res.task_id == "t1"
    assert task_res.status is TaskStatus.COMPLETED
    assert len(task_res.evidence) == 1
    assert task_res.evidence[0].content.startswith("Free slot")


def test_delegation_context_and_result() -> None:
    """Verify DelegationContext frozen scoping and DelegationResult approval status."""
    ctx = DelegationContext(
        parent_agent="Supervisor",
        target_agent="CommunicationAgent",
        delegation_depth=1,
        allowed_tools=["gmail.search"],
        read_only=True,
    )
    assert ctx.parent_agent == "Supervisor"
    assert ctx.target_agent == "CommunicationAgent"
    assert ctx.delegation_depth == 1
    assert ctx.allowed_tools == ["gmail.search"]
    assert ctx.read_only is True

    # Blank agent names are rejected
    with pytest.raises(ValueError):
        DelegationContext(parent_agent=" ", target_agent="CommAgent")

    # DelegationResult (standalone P11 shape)
    del_res = DelegationResult(
        agent_name="CommunicationAgent",
        success=True,
        output="Done",
        needs_approval=True,
        delegation_depth=1,
    )
    assert del_res.needs_approval is True
    assert del_res.delegation_depth == 1
    assert del_res.success is True

    # AgentDefinition delegation policy metadata
    agent_def = AgentDefinition(
        name="CommunicationAgent",
        description="Handles email and contacts",
        domain=Domain.COMMUNICATION,
        delegation_allowed=True,
        max_child_depth=2,
        inherits_parent_tools=False,
    )
    assert agent_def.delegation_allowed is True
    assert agent_def.max_child_depth == 2
    assert agent_def.inherits_parent_tools is False

    with pytest.raises(ValueError, match="max_child_depth must be non-negative"):
        AgentDefinition(
            name="BadAgent",
            description="Bad agent",
            domain=Domain.COMMUNICATION,
            max_child_depth=-1,
        )
