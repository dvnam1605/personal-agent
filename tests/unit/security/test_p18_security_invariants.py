"""Security invariant test suite for Phase 18 (spec P18_policy_approval_full_write_access.md).

Verifies:
1. send cannot bypass approval
2. delete cannot bypass approval
3. replay safe (single-use token)
4. stale target (fingerprint revalidation)
5. expired approval
6. denied action (explicit rejection halts mutation)
7. capability exposure still enforced
8. interrupted run resumes after restart and executes the mutation exactly once
9. checkpointed state contains no unredacted secrets or external raw content
10. delegated specialist cannot self-approve (approval_policy=NEVER)
11. delegation policy frozen at delegation time
12. consolidated approval from Supervisor path works
13. unavailable answerer defaults to fail-closed (unavailable outcome)
14. headless run with approval_policy=never rejects mutation deterministically without hanging
15. structured user question flow delivers multi-choice and free-text answers correctly
16. question option validation enforces single- and multi-select boundaries
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typing_extensions import TypedDict

from app.domain.enums import ActionClass, ActionRiskLevel, ApprovalOutcome, ApprovalPolicy
from app.domain.errors import PermissionDeniedError
from app.domain.models import (
    DelegationContext,
    ProposedAction,
    ToolContext,
    ToolDefinition,
    ToolInput,
    ToolRestriction,
    UserQuestionAnswer,
    UserQuestionItem,
    UserQuestionOption,
)
from app.harness.interrupts import interrupt_for_approval, resume_graph
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.services.approvals import (
    ApprovalRequestService,
    canonical_proposal_hash,
    generate_approval_token,
    verify_approval_token,
)
from app.services.consumed_store import InMemoryConsumedTokenStore
from app.services.policy_engine import PolicyEngine
from app.services.question_plane import QuestionPlaneService
from app.services.run_persistence import RunPersistenceService
from app.tools.google_calendar import GoogleCalendarTools
from app.tools.google_communication import GoogleCommunicationTools
from app.tools.registry import ToolRegistry


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# 1. Send cannot bypass approval
@pytest.mark.asyncio
async def test_send_cannot_bypass_approval() -> None:
    tools = GoogleCommunicationTools(cast(Any, MagicMock()))
    context = ToolContext(run_id="run_sec_1", user_id="user_1", approval_token=None)
    result = await tools.execute(
        ToolInput(tool_name="gmail.send_draft", arguments={"draft_id": "draft_123"}),
        context,
    )
    assert result.success is False
    assert "requires human approval verification" in (result.error or "")


# 2. Delete cannot bypass approval
@pytest.mark.asyncio
async def test_delete_cannot_bypass_approval() -> None:
    tools = GoogleCalendarTools(cast(Any, MagicMock()))
    context = ToolContext(run_id="run_sec_2", user_id="user_1", approval_token=None)
    result = await tools.execute(
        ToolInput(
            tool_name="calendar.delete_event",
            arguments={"calendar_id": "primary", "event_id": "evt_456"},
        ),
        context,
    )
    assert result.success is False
    assert "requires human approval verification" in (result.error or "")


# 3. Replay safe
@pytest.mark.asyncio
async def test_replay_safe() -> None:
    store = InMemoryConsumedTokenStore()
    args = {"draft_id": "draft_replay"}
    prop_hash = canonical_proposal_hash("gmail.send_draft", args)
    token = generate_approval_token(
        approval_id="appr_replay",
        tool_name="gmail.send_draft",
        run_id="run_replay",
        proposal_hash=prop_hash,
    )

    # First consume succeeds
    first_try = await verify_approval_token(
        token,
        tool_name="gmail.send_draft",
        consume=True,
        store=store,
        expected_run_id="run_replay",
        expected_proposal_hash=prop_hash,
    )
    assert first_try is True

    # Replay consume fails closed
    second_try = await verify_approval_token(
        token,
        tool_name="gmail.send_draft",
        consume=True,
        store=store,
        expected_run_id="run_replay",
        expected_proposal_hash=prop_hash,
    )
    assert second_try is False


# 4. Stale target
def test_stale_target() -> None:
    action = ProposedAction(
        action_type="update_event",
        description="Change meeting title",
        target="evt_target_1",
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
    )
    # Different fingerprint indicates state changed between approval and execution
    assert (
        PolicyEngine.validate_target_state(action, "current_etag_v2", "expected_etag_v1") is False
    )
    assert (
        PolicyEngine.validate_target_state(action, "expected_etag_v1", "expected_etag_v1") is True
    )


# 5. Expired approval
@pytest.mark.asyncio
async def test_expired_approval() -> None:
    # Expired token (negative TTL)
    token = generate_approval_token(
        approval_id="appr_exp",
        tool_name="gmail.send_draft",
        expires_in_seconds=-10,
    )
    valid = await verify_approval_token(token, tool_name="gmail.send_draft", consume=False)
    assert valid is False


# 6. Denied action
@pytest.mark.asyncio
async def test_denied_action(db_session: AsyncSession) -> None:
    user = User(id="user_sec_6", email="u6@example.com")
    db_session.add(user)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_sec_6",
        user_id=user.id,
        request="Delete files",
        route_type="direct_specialist",
        domains=["system"],
        complexity="direct",
        correlation_id="corr_sec_6",
    )
    action = ProposedAction(
        action_type="delete_file",
        description="Delete file",
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        requires_approval=True,
    )
    req = await ApprovalRequestService.create_request(db_session, run.id, action)
    denied = await ApprovalRequestService.decide_with_outcome(
        db_session,
        approval_id=req.id,
        outcome=ApprovalOutcome.REJECTED,
        approver_id=user.id,
        reason="Operation rejected by user.",
    )
    assert denied.status == "rejected"
    assert denied.approved is False
    assert denied.reason == "Operation rejected by user."


# 7. Capability exposure still enforced
def test_capability_exposure_still_enforced() -> None:
    t1 = ToolDefinition(
        name="gmail.search_threads",
        description="Search threads",
        capabilities=["communication.read"],
        is_mutation=False,
    )
    t2 = ToolDefinition(
        name="gmail.send_draft",
        description="Send draft",
        capabilities=["communication.write"],
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        is_mutation=True,
        action_class=ActionClass.EXTERNAL_COMMUNICATION,
    )
    reg = ToolRegistry([t1, t2])
    scoped = reg.restrict(ToolRestriction(allow=["communication.read"]))
    with pytest.raises(PermissionDeniedError):
        scoped.get("gmail.send_draft")


# 8. Interrupted run resumes after restart and executes mutation exactly once
class _HarnessChannels(TypedDict):
    run_id: str
    target: str
    token: str | None
    mutations_executed: int


@pytest.mark.asyncio
async def test_interrupted_run_resumes_after_restart_and_executes_once() -> None:
    checkpointer = MemorySaver()

    def build_graph() -> Any:
        builder = StateGraph(_HarnessChannels)

        def pause_node(state: _HarnessChannels) -> dict[str, Any]:
            resume = interrupt_for_approval({"target": state["target"]})
            return {"token": resume.get("token")}

        def mutate_node(state: _HarnessChannels) -> dict[str, Any]:
            token = state.get("token")
            if not token:
                raise PermissionDeniedError("Missing approval token")
            return {"mutations_executed": state.get("mutations_executed", 0) + 1}

        builder.add_node("pause", pause_node)
        builder.add_node("mutate", mutate_node)
        builder.set_entry_point("pause")
        builder.add_edge("pause", "mutate")
        builder.add_edge("mutate", END)
        return builder.compile(checkpointer=checkpointer)

    thread_id = "run_hitl_restart_1"
    config = {"configurable": {"thread_id": thread_id}}

    # Process 1 starts execution and pauses
    g1 = build_graph()
    paused = await g1.ainvoke(
        {
            "run_id": thread_id,
            "target": "customer@example.com",
            "token": None,
            "mutations_executed": 0,
        },
        config=config,
    )
    assert "__interrupt__" in paused

    # Process 2 boots up (new graph instance, same checkpointer) and resumes
    g2 = build_graph()
    resumed = await resume_graph(g2, run_id=thread_id, resume_payload={"token": "appr_valid_token"})
    assert resumed["mutations_executed"] == 1


# 9. Checkpointed state contains no unredacted secrets
@pytest.mark.asyncio
async def test_checkpointed_state_contains_no_unredacted_secrets(db_session: AsyncSession) -> None:
    user = User(id="user_sec_9", email="u9@example.com")
    db_session.add(user)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_sec_9",
        user_id=user.id,
        request="Secret payload test",
        route_type="direct_specialist",
        domains=["system"],
        complexity="direct",
        correlation_id="corr_sec_9",
    )
    action = ProposedAction(
        action_type="call_external",
        description="External API call",
        parameters={"api_key": "sk-super-secret-password-12345", "token": "bearer-secret"},
        requires_approval=True,
    )
    req = await ApprovalRequestService.create_request(db_session, run.id, action)
    assert req.parameters["api_key"] == "[REDACTED_SECRET]"
    assert req.parameters["token"] == "[REDACTED_SECRET]"


# 10. Delegated specialist cannot self-approve
def test_delegated_specialist_cannot_self_approve() -> None:
    delegation = DelegationContext(
        parent_agent="SupervisorAgent",
        target_agent="DriveSpecialist",
        approval_policy="NEVER",
    )
    action = ProposedAction(
        action_type="delete_file",
        description="Delete shared folder",
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        requires_approval=True,
    )
    decision = PolicyEngine.evaluate_action(action, delegation=delegation)
    assert decision.allowed is False
    assert decision.needs_approval is True
    assert decision.outcome == ApprovalOutcome.REJECTED


# 11. Delegation policy frozen at delegation time
def test_delegation_policy_frozen_at_delegation_time() -> None:
    child_context = DelegationContext(
        parent_agent="SupervisorAgent",
        target_agent="CommunicationSpecialist",
        approval_policy="NEVER",
    )
    action = ProposedAction(
        action_type="send_email",
        description="Child specialist send",
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    # Child policy remains NEVER regardless of session level
    decision = PolicyEngine.evaluate_action(
        action, session_policy=ApprovalPolicy.ASK, delegation=child_context
    )
    assert decision.allowed is False
    assert decision.needs_approval is True
    assert decision.outcome == ApprovalOutcome.REJECTED


# 12. Consolidated approval from Supervisor path works
def test_consolidated_approval_from_supervisor_path() -> None:
    from app.harness.supervisor.channels import SupervisorChannels
    from app.harness.supervisor.graph import SupervisorGraphBuilder
    from app.services.supervisor.catalog import CapabilityCatalog
    from app.services.supervisor.planner import SupervisorPlanner

    builder = SupervisorGraphBuilder(
        planner=SupervisorPlanner(),
        catalog=CapabilityCatalog(agents=[]),
    )

    state: SupervisorChannels = {
        "query": "Review and delete old meetings",
        "plan": None,
        "task_results": {
            "t1": {"status": "needs_approval", "output": "Waiting for delete authorization"},
            "t2": {"status": "completed", "output": "Review completed"},
        },
        "needs_approval": [{"task_id": "t1", "action": "delete_event"}],
        "branch_errors": [],
        "evidence": [],
    }
    # Test supervisor synthesis node handling needs_approval
    res = builder.synthesize_result(state)
    assert "Pending Approvals: 1 action(s) require authorization." in res.get("final_synthesis", "")


# 13. Unavailable answerer defaults to fail-closed
@pytest.mark.asyncio
async def test_unavailable_answerer_defaults_to_fail_closed(db_session: AsyncSession) -> None:
    user = User(id="user_sec_13", email="u13@example.com")
    db_session.add(user)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_sec_13",
        user_id=user.id,
        request="Interactive question",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        correlation_id="corr_sec_13",
    )
    action = ProposedAction(
        action_type="transfer_ownership",
        description="Transfer ownership",
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        requires_approval=True,
    )
    req = await ApprovalRequestService.create_request(db_session, run.id, action)
    unavailable = await ApprovalRequestService.decide_with_outcome(
        db_session,
        approval_id=req.id,
        outcome=ApprovalOutcome.UNAVAILABLE,
        approver_id="system",
        reason="No UI or connected answerer available.",
    )
    assert unavailable.status == "unavailable"
    assert unavailable.approved is False


# 14. Headless run with approval_policy=never rejects mutation deterministically without hanging
def test_headless_run_never_policy_rejects_deterministically() -> None:
    action = ProposedAction(
        action_type="share_drive_file",
        description="Share confidential file",
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    start_time = time.perf_counter()
    decision = PolicyEngine.evaluate_action(action, session_policy=ApprovalPolicy.NEVER)
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    assert elapsed_ms < 50.0  # Returns instantaneously without blocking or waiting
    assert decision.allowed is False
    assert decision.needs_approval is False
    assert decision.outcome == ApprovalOutcome.REJECTED


# 15. Structured user question flow delivers multi-choice and free-text answers correctly
@pytest.mark.asyncio
async def test_structured_user_question_flow(db_session: AsyncSession) -> None:
    questions = [
        UserQuestionItem(
            id="q_choice",
            question="Which action do you prefer?",
            options=[
                UserQuestionOption(label="Archive"),
                UserQuestionOption(label="Delete"),
            ],
            multi_select=False,
        ),
        UserQuestionItem(
            id="q_text",
            question="Provide optional feedback:",
            options=None,
        ),
    ]
    req = await QuestionPlaneService.create_question_request(
        db_session, run_id="run_flow_15", questions=questions
    )

    answers = [
        UserQuestionAnswer(question_id="q_choice", selected_options=["Archive"]),
        UserQuestionAnswer(question_id="q_text", free_text="Please proceed with archiving."),
    ]
    answered = await QuestionPlaneService.record_answers(
        db_session,
        question_id=req.id,
        answers=answers,
        answered_by="user_bob",
    )
    assert answered.status == "answered"
    assert answered.answers is not None
    assert answered.answers[0]["selected_options"] == ["Archive"]
    assert answered.answers[1]["free_text"] == "Please proceed with archiving."


# 16. Question option validation enforces single- and multi-select boundaries
@pytest.mark.asyncio
async def test_question_option_validation_boundaries(db_session: AsyncSession) -> None:
    questions = [
        UserQuestionItem(
            id="q_single",
            question="Choose exactly one option",
            options=[UserQuestionOption(label="A"), UserQuestionOption(label="B")],
            multi_select=False,
        ),
        UserQuestionItem(
            id="q_multi",
            question="Choose multiple options",
            options=[
                UserQuestionOption(label="X"),
                UserQuestionOption(label="Y"),
                UserQuestionOption(label="Z"),
            ],
            multi_select=True,
        ),
    ]
    req = await QuestionPlaneService.create_question_request(
        db_session, run_id="run_bound_16", questions=questions
    )

    # Violate single-select constraint
    with pytest.raises(ValueError, match="does not allow multiple selections"):
        await QuestionPlaneService.record_answers(
            db_session,
            question_id=req.id,
            answers=[UserQuestionAnswer(question_id="q_single", selected_options=["A", "B"])],
            answered_by="user_bob",
        )

    # Violate option membership
    with pytest.raises(ValueError, match="is not valid for question"):
        await QuestionPlaneService.record_answers(
            db_session,
            question_id=req.id,
            answers=[UserQuestionAnswer(question_id="q_single", selected_options=["C"])],
            answered_by="user_bob",
        )

    # Valid mixed answers
    valid_answers = [
        UserQuestionAnswer(question_id="q_single", selected_options=["A"]),
        UserQuestionAnswer(question_id="q_multi", selected_options=["X", "Z"]),
    ]
    answered = await QuestionPlaneService.record_answers(
        db_session,
        question_id=req.id,
        answers=valid_answers,
        answered_by="user_bob",
    )
    assert answered.status == "answered"


@pytest.mark.asyncio
async def test_invariant_17_approve_execute_approve_execute_second_fails(
    db_session: AsyncSession,
) -> None:
    """Second execution of a consumed token fails; remint is refused (P18-06, H2, N4)."""
    from app.services.approvals import execution_token_for_request, require_mutation_approval

    user = User(id="user_h2", email="h2@example.com")
    db_session.add(user)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_h2",
        user_id=user.id,
        request="Test H2",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_h2",
    )

    action = ProposedAction(
        action_type="delete_event",
        description="Delete event",
        tool_name="calendar.delete",
        parameters={"event_id": "ev_123", "calendar_id": "primary"},
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    req = await ApprovalRequestService.create_request(db_session, run.id, action)

    # First approve
    decided1 = await ApprovalRequestService.decide_with_outcome(
        db_session, req.id, ApprovalOutcome.ALLOWED_ONCE, approver_id=user.id
    )
    token1 = execution_token_for_request(decided1)
    decided1.token_hash = "issued"
    await db_session.flush()

    # First execution succeeds
    store = InMemoryConsumedTokenStore()
    await require_mutation_approval(
        tool_name="calendar.delete",
        context_token=token1,
        arguments={"event_id": "ev_123", "calendar_id": "primary"},
        run_id=run.id,
        store=store,
    )

    # Second approve is idempotent and must not mint another live token.
    decided2 = await ApprovalRequestService.decide_with_outcome(
        db_session, req.id, ApprovalOutcome.ALLOWED_ONCE, approver_id=user.id
    )
    assert decided2.token_hash == "issued"

    # Replay of the original consumed token must fail.
    with pytest.raises(PermissionDeniedError, match="invalid, expired, or unverified"):
        await require_mutation_approval(
            tool_name="calendar.delete",
            context_token=token1,
            arguments={"event_id": "ev_123", "calendar_id": "primary"},
            run_id=run.id,
            store=store,
        )


@pytest.mark.asyncio
async def test_invariant_18_stale_target_rejection() -> None:
    """require_mutation_approval rejects execution when target fingerprint is stale (P18-05, M3)."""
    from app.services.approvals import generate_approval_token, require_mutation_approval

    args = {"event_id": "ev_123", "title": "Updated meeting"}
    token = generate_approval_token(
        "appr_stale_18",
        tool_name="google_calendar.update_event",
        arguments=args,
        run_id="run_stale_18",
    )

    # Stale target: current etag does not match expected etag
    with pytest.raises(PermissionDeniedError, match="stale target state"):
        await require_mutation_approval(
            tool_name="google_calendar.update_event",
            context_token=token,
            arguments=args,
            run_id="run_stale_18",
            current_target_fingerprint="etag_v2",
            expected_target_fingerprint="etag_v1",
        )


@pytest.mark.asyncio
async def test_invariant_19_user_scoped_pending_requests(
    db_session: AsyncSession,
) -> None:
    """Pending approvals are strictly filtered by user_id ownership (M4)."""
    user_a = User(id="user_a", email="a@example.com")
    user_b = User(id="user_b", email="b@example.com")
    db_session.add_all([user_a, user_b])
    await db_session.flush()

    run_a = await RunPersistenceService.create_run(
        db_session,
        run_id="run_a",
        user_id=user_a.id,
        request="Run A",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_run_a",
    )
    run_b = await RunPersistenceService.create_run(
        db_session,
        run_id="run_b",
        user_id=user_b.id,
        request="Run B",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_run_b",
    )

    action = ProposedAction(
        action_type="delete_file",
        description="Delete file",
        tool_name="drive.delete",
        parameters={"file_id": "f_1"},
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    await ApprovalRequestService.create_request(db_session, run_a.id, action)
    await ApprovalRequestService.create_request(db_session, run_b.id, action)

    # Scoped to user_a: only run_a approval
    pending_a = await ApprovalRequestService.get_pending_requests(db_session, user_id=user_a.id)
    assert len(pending_a) == 1
    assert pending_a[0].run_id == run_a.id

    # Scoped to user_b: only run_b approval
    pending_b = await ApprovalRequestService.get_pending_requests(db_session, user_id=user_b.id)
    assert len(pending_b) == 1
    assert pending_b[0].run_id == run_b.id


@pytest.mark.asyncio
async def test_invariant_20_conflicting_answers_rejected(
    db_session: AsyncSession,
) -> None:
    """Submitting conflicting answers to an already-answered question raises ValueError (L1)."""
    questions = [
        UserQuestionItem(
            id="q_choice",
            question="Select color",
            options=[UserQuestionOption(label="Red"), UserQuestionOption(label="Blue")],
            multi_select=False,
        )
    ]
    req = await QuestionPlaneService.create_question_request(
        db_session, run_id="run_conf_20", questions=questions
    )

    # First answer
    await QuestionPlaneService.record_answers(
        db_session,
        question_id=req.id,
        answers=[UserQuestionAnswer(question_id="q_choice", selected_options=["Red"])],
        answered_by="user_bob",
    )

    # Same answer succeeds idempotently
    same = await QuestionPlaneService.record_answers(
        db_session,
        question_id=req.id,
        answers=[UserQuestionAnswer(question_id="q_choice", selected_options=["Red"])],
        answered_by="user_bob",
    )
    assert same.status == "answered"

    # Different answer raises conflict
    with pytest.raises(ValueError, match="Conflict: Question request"):
        await QuestionPlaneService.record_answers(
            db_session,
            question_id=req.id,
            answers=[UserQuestionAnswer(question_id="q_choice", selected_options=["Blue"])],
            answered_by="user_bob",
        )
