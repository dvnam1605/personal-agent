"""Unit tests for first-party PolicyEngine (spec P18-01, P18-01A, P18-05)."""

from __future__ import annotations

from app.domain.enums import ActionRiskLevel, ApprovalOutcome, ApprovalPolicy
from app.domain.models import DelegationContext, ProposedAction
from app.services.approvals.policy_engine import PolicyEngine


def test_read_only_action_pre_approved() -> None:
    action = ProposedAction(
        action_type="read_email",
        description="Read user email",
        risk_level=ActionRiskLevel.READ_ONLY,
        requires_approval=False,
    )
    decision = PolicyEngine.evaluate_action(action, session_policy=ApprovalPolicy.ASK)
    assert decision.allowed is True
    assert decision.needs_approval is False
    assert decision.outcome is None


def test_invalid_policy_fails_closed() -> None:
    action = ProposedAction(
        action_type="send_email",
        description="Send email",
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    decision = PolicyEngine.evaluate_action(action, session_policy="invalid_policy_garbage")  # type: ignore[arg-type]
    assert decision.allowed is False
    assert decision.needs_approval is False
    assert decision.outcome == ApprovalOutcome.REJECTED


def test_mutation_in_interactive_session_needs_approval() -> None:
    action = ProposedAction(
        action_type="send_email",
        description="Send follow-up email",
        target="bob@example.com",
        important_arguments={"to": "bob@example.com"},
        tool_name="gmail.send",
        parameters={"to": "bob@example.com", "subject": "Hello"},
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    decision = PolicyEngine.evaluate_action(action, session_policy=ApprovalPolicy.ASK)
    assert decision.allowed is False
    assert decision.needs_approval is True
    assert decision.outcome is None


def test_headless_run_never_policy_auto_denies_without_hanging() -> None:
    action = ProposedAction(
        action_type="delete_calendar_event",
        description="Delete conflicting meeting",
        target="event_999",
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    decision = PolicyEngine.evaluate_action(action, session_policy=ApprovalPolicy.NEVER)
    assert decision.allowed is False
    assert decision.needs_approval is False
    assert decision.outcome == ApprovalOutcome.REJECTED
    assert "NEVER" in decision.reason


def test_delegated_specialist_cannot_self_approve() -> None:
    delegation = DelegationContext(
        parent_agent="SupervisorAgent",
        target_agent="CommunicationSpecialist",
        approval_policy="NEVER",
    )
    action = ProposedAction(
        action_type="send_email",
        description="Send an email from child specialist",
        target="charlie@example.com",
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    decision = PolicyEngine.evaluate_action(
        action, session_policy=ApprovalPolicy.ASK, delegation=delegation
    )
    assert decision.allowed is False
    assert decision.needs_approval is True
    assert decision.outcome == ApprovalOutcome.REJECTED
    assert "cannot self-approve" in decision.reason


def test_delegation_policy_frozen_at_delegation_time() -> None:
    frozen_delegation = DelegationContext(
        parent_agent="SupervisorAgent",
        target_agent="CalendarSpecialist",
        approval_policy="NEVER",
    )
    action = ProposedAction(
        action_type="create_event",
        description="Schedule a meeting",
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        requires_approval=True,
    )
    # Even if parent runtime is in interactive ASK mode, child policy remains NEVER
    decision = PolicyEngine.evaluate_action(
        action, session_policy=ApprovalPolicy.ASK, delegation=frozen_delegation
    )
    assert decision.allowed is False
    assert decision.needs_approval is True
    assert decision.outcome == ApprovalOutcome.REJECTED


def test_stale_target_validation() -> None:
    action = ProposedAction(
        action_type="update_event",
        description="Update event description",
        target="event_123",
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
    )
    # Matching target state: valid
    assert (
        PolicyEngine.validate_target_state(
            action, current_fingerprint="etag_v1", expected_fingerprint="etag_v1"
        )
        is True
    )

    # Modified target state: stale
    assert (
        PolicyEngine.validate_target_state(
            action, current_fingerprint="etag_v2", expected_fingerprint="etag_v1"
        )
        is False
    )

    # No expected fingerprint on a non-fingerprint tool: skip (create-style / unlabeled)
    assert (
        PolicyEngine.validate_target_state(
            action, current_fingerprint="etag_v2", expected_fingerprint=None
        )
        is True
    )

    fingerprint_action = ProposedAction(
        action_type="calendar.update_event",
        description="Update event",
        tool_name="calendar.update_event",
        target="event_123",
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
    )
    assert (
        PolicyEngine.validate_target_state(
            fingerprint_action, current_fingerprint=None, expected_fingerprint=None
        )
        is False
    )
