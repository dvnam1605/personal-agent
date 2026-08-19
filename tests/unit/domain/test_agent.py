"""Unit tests for Agent communication contracts."""

from app.domain.enums import EvidenceType, TaskStatus
from app.domain.models import (
    AgentRequest,
    AgentResult,
    CapabilityRequest,
    EvidenceItem,
    EvidenceSource,
    ExecutionBudget,
    NeedMoreContext,
    TaskResult,
)


def test_agent_request_creation() -> None:
    """Verify AgentRequest structure and tool access gating."""
    req = AgentRequest(
        run_id="run_abc",
        agent_name="CalendarAgent",
        goal="Find free slots on next Tuesday after 2 PM",
        context_data={"user_timezone": "Asia/Ho_Chi_Minh"},
        available_tools=["calendar.list_events", "calendar.get_free_busy"],
        budget=ExecutionBudget(max_llm_calls=2, max_tool_calls=4),
    )
    assert req.run_id == "run_abc"
    assert req.agent_name == "CalendarAgent"
    assert len(req.available_tools) == 2
    assert req.budget.max_llm_calls == 2


def test_need_more_context() -> None:
    """Verify clarification request model."""
    nmc = NeedMoreContext(
        question="Which attendee do you mean by 'Nam'?",
        missing_fields=["attendee_email"],
        suggested_source="contacts",
    )
    assert nmc.question.startswith("Which attendee")
    assert nmc.missing_fields == ["attendee_email"]
    assert nmc.suggested_source == "contacts"


def test_capability_request() -> None:
    """Verify delegation / capability escalation model."""
    cap = CapabilityRequest(
        requested_capability="calendar.create_event",
        target_agent="CalendarAgent",
        reason="CommunicationAgent found agreed meeting time in email thread",
    )
    assert cap.requested_capability == "calendar.create_event"
    assert cap.target_agent == "CalendarAgent"


def test_agent_result_with_tasks_and_evidence() -> None:
    """Verify comprehensive AgentResult with tasks and evidence."""
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
    agent_res = AgentResult(
        agent_name="CalendarAgent",
        success=True,
        output="I found a free slot on Tuesday from 14:00 to 15:00.",
        task_results=[task_res],
        new_evidence=[evidence],
    )

    assert agent_res.success is True
    assert len(agent_res.task_results) == 1
    assert agent_res.task_results[0].status == TaskStatus.COMPLETED
    assert len(agent_res.new_evidence) == 1
