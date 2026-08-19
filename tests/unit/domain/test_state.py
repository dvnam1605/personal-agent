"""Unit tests for AssistantState, assignment safety, and state transitions."""

import pytest
from pydantic import ValidationError

from app.domain.enums import (
    ActionRiskLevel,
    Complexity,
    Domain,
    EvidenceType,
    RouteType,
    RunStatus,
    TaskStatus,
)
from app.domain.models import (
    ActionApproval,
    AssistantState,
    EvidenceItem,
    EvidenceSource,
    ExecutionPlan,
    ExecutionTask,
    ProposedAction,
    RouteDecision,
    TaskResult,
)


def test_assistant_state_minimal_init() -> None:
    """Verify default values upon minimal state initialization."""
    state = AssistantState(
        user_id="user_123",
        request="Check my emails from today",
    )
    assert state.run_id is not None
    assert state.user_id == "user_123"
    assert state.request == "Check my emails from today"
    assert state.status == RunStatus.PENDING
    assert state.iteration == 0
    assert state.react_steps == 0
    assert state.tool_call_count == 0
    assert state.llm_call_count == 0
    assert state.evidence == []
    assert state.task_results == []
    assert state.proposed_actions == []
    assert state.approvals == []
    assert state.errors == []


def test_assistant_state_assignment_validation() -> None:
    """Verify validate_assignment prevents assigning invalid statuses or corrupted types."""
    state = AssistantState(
        user_id="user_123",
        request="Check my emails",
    )
    # Valid assignment
    state.status = RunStatus.RUNNING
    assert state.status == RunStatus.RUNNING

    # Invalid status assignment must be rejected
    with pytest.raises(ValidationError):
        state.status = "unknown_status"  # type: ignore[assignment]

    with pytest.raises(ValidationError):
        state.iteration = -5  # ge=0 violation


def test_assistant_state_add_evidence_and_errors() -> None:
    """Verify mutating state with evidence and recording errors."""
    state = AssistantState(
        user_id="user_123",
        request="Summarize emails",
        status=RunStatus.RUNNING,
    )
    source = EvidenceSource(source_type="gmail", source_id="msg_99")
    item = EvidenceItem(
        evidence_type=EvidenceType.EMAIL,
        content="Meeting rescheduled",
        source=source,
    )
    state.add_evidence(item)
    assert len(state.evidence) == 1

    state.record_error("Failed to connect to Google API")
    assert len(state.errors) == 1
    assert state.status == RunStatus.FAILED


def test_assistant_state_full_roundtrip_serialization() -> None:
    """Verify complex nested state with typed actions serializes and deserializes accurately."""
    route = RouteDecision(
        domains=[Domain.COMMUNICATION],
        complexity=Complexity.ADAPTIVE,
        route_type=RouteType.DIRECT_SPECIALIST,
        confidence=0.95,
    )
    task1 = ExecutionTask(
        id="t1",
        name="Search",
        assigned_agent="CommunicationAgent",
        description="Search recent emails",
        status=TaskStatus.COMPLETED,
    )
    plan = ExecutionPlan(
        goal="Process communication",
        tasks=[task1],
    )
    source = EvidenceSource(source_type="gmail", source_id="msg_1")
    evidence = EvidenceItem(
        evidence_type=EvidenceType.EMAIL,
        content="Content snippet",
        source=source,
    )
    task_res = TaskResult(
        task_id="t1",
        status=TaskStatus.COMPLETED,
        result_data={"found": 1},
    )
    action = ProposedAction(
        id="act_001",
        action_type="send_email",
        description="Send confirmation email to Nam",
        tool_name="gmail.send_message",
        parameters={"to": "nam@example.com", "body": "Confirmed"},
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
    )
    approval = ActionApproval(
        action_id="act_001",
        approved=True,
        approver_id="usr_55",
        reason="Looks good",
    )

    state = AssistantState(
        run_id="custom_run_id_001",
        user_id="usr_55",
        request="Find latest updates",
        normalized_request="find latest updates from team",
        route_decision=route,
        goal="Retrieve email updates",
        entities={"person": "Alice"},
        active_skill="email_reader",
        active_workflow=None,
        plan=plan,
        task_results=[task_res],
        evidence=[evidence],
        missing_information=["optional_date"],
        proposed_actions=[action],
        approvals=[approval],
        iteration=1,
        react_steps=2,
        tool_call_count=3,
        llm_call_count=1,
        status=RunStatus.RUNNING,
        errors=[],
    )

    json_str = state.model_dump_json()
    reloaded = AssistantState.model_validate_json(json_str)

    assert reloaded.run_id == "custom_run_id_001"
    assert reloaded.user_id == "usr_55"
    assert reloaded.route_decision is not None
    assert reloaded.route_decision.domains == [Domain.COMMUNICATION]
    assert reloaded.plan is not None
    assert len(reloaded.plan.tasks) == 1
    assert len(reloaded.evidence) == 1
    assert reloaded.evidence[0].source.source_id == "msg_1"
    assert len(reloaded.proposed_actions) == 1
    assert reloaded.proposed_actions[0].action_type == "send_email"
    assert len(reloaded.approvals) == 1
    assert reloaded.approvals[0].approved is True
    assert reloaded.status == RunStatus.RUNNING


def test_assistant_state_extra_forbidden() -> None:
    """Verify unknown fields are rejected."""
    with pytest.raises(ValidationError):
        AssistantState(
            user_id="user_123",
            request="test",
            unauthorized_field="malicious",  # type: ignore[call-arg]
        )
