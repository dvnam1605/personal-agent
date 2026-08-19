"""Unit tests for RunPersistenceService and AuditService integration."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.enums import Complexity, Domain, RouteType, RunStatus, TaskStatus
from app.domain.models import (
    AssistantState,
    BudgetUsage,
    ExecutionPlan,
    ExecutionTask,
    ProposedAction,
    RouteDecision,
    TaskResult,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import AuditOutbox, LLMExecution, ToolExecution, User
from app.services.audit import AuditOutboxService, AuditService
from app.services.run_persistence import RunPersistenceService


@pytest.fixture
async def db_session():
    """Create in-memory SQLite async session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_full_run_lifecycle_persistence(db_session: AsyncSession) -> None:
    """Verify end-to-end run lifecycle: create -> update state -> log audit events -> complete."""
    # 1. Create User
    user = User(email="runner@example.com", full_name="Runner")
    db_session.add(user)
    await db_session.flush()

    run_id = "run_life_1"
    corr_id = "corr_life_1"

    # 2. Create Run
    run = await RunPersistenceService.create_run(
        session=db_session,
        run_id=run_id,
        user_id=user.id,
        request="Send meeting invite to Nam",
        route_type="direct_specialist",
        domains=["calendar"],
        complexity="direct",
        correlation_id=corr_id,
    )
    assert run.id == run_id
    assert run.correlation_id == corr_id
    assert run.status == RunStatus.RUNNING.value
    assert run.telemetry_degraded is False

    # 3. Update Run State
    route = RouteDecision(
        domains=[Domain.CALENDAR],
        complexity=Complexity.DIRECT,
        route_type=RouteType.DIRECT_SPECIALIST,
        confidence=0.95,
    )
    state = AssistantState(
        run_id=run_id,
        user_id=user.id,
        request="Send meeting invite to Nam",
        route_decision=route,
        iteration=1,
        react_steps=1,
        tool_call_count=2,
        llm_call_count=1,
        status=RunStatus.RUNNING,
    )

    updated_run = await RunPersistenceService.update_run_state(db_session, run_id, state)
    assert updated_run is not None
    assert updated_run.llm_call_count == 1
    assert updated_run.tool_call_count == 2
    assert updated_run.react_steps == 1
    assert updated_run.iteration == 1
    assert updated_run.state_snapshot is not None

    # 4. Append Tool and LLM Audit Records
    tool_rec = await AuditService.record_tool_execution(
        session=db_session,
        run_id=run_id,
        tool_name="calendar.create_event",
        input_parameters={"summary": "Sync with Nam", "secret_token": "abc123xyz"},
        success=True,
        latency_ms=250.0,
    )
    assert tool_rec is not None
    assert tool_rec.payload["input_parameters"]["secret_token"] == "[REDACTED_SECRET]"

    llm_rec = await AuditService.record_llm_execution(
        session=db_session,
        run_id=run_id,
        provider="google",
        model="gemini-1.5-pro",
        purpose="agent_execution",
        prompt_tokens=500,
        completion_tokens=100,
        total_tokens=600,
        estimated_cost_usd=Decimal("0.001500"),
        latency_ms=800.0,
        success=True,
    )
    assert llm_rec is not None
    assert llm_rec.payload["total_tokens"] == 600

    delivered = await AuditOutboxService.deliver_pending(db_session, run_id=run_id)
    assert delivered == 2
    assert (await db_session.execute(select(ToolExecution))).scalar_one().tool_name == "calendar.create_event"
    assert (await db_session.execute(select(LLMExecution))).scalar_one().total_tokens == 600

    # 5. Complete Run
    usage = BudgetUsage(llm_calls=1, tool_calls=2, react_steps=1)
    completed_run = await RunPersistenceService.complete_run(
        session=db_session,
        run_id=run_id,
        status=RunStatus.COMPLETED,
        total_latency_ms=4550.0,
        budget_usage=usage,
        state=state,
        prompt_tokens=500,
        completion_tokens=100,
        total_tokens=600,
        estimated_cost_usd=Decimal("0.001500"),
    )
    assert completed_run is not None
    assert completed_run.status == RunStatus.COMPLETED.value
    assert completed_run.completed_at is not None
    assert completed_run.total_latency_ms == 4550.0
    assert completed_run.estimated_cost_usd == Decimal("0.001500")


@pytest.mark.asyncio
async def test_audit_outbox_is_durable_and_materialized(db_session: AsyncSession) -> None:
    """Verify audit records persist durably before their append-only projection is delivered."""
    user = User(email="outbox_user@example.com")
    db_session.add(user)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        session=db_session,
        run_id="run_outbox_1",
        user_id=user.id,
        request="test outbox",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        correlation_id="corr_outbox_1",
    )

    pending = await AuditService.record_tool_execution(
        session=db_session,
        run_id=run.id,
        tool_name="buffered_tool",
        input_parameters={"arg": 1},
        output_summary={"res": "ok"},
        success=True,
        latency_ms=10.0,
    )
    assert pending.status == "pending"
    assert await AuditOutboxService.has_pending(db_session, run.id) is True

    delivered = await AuditOutboxService.deliver_pending(db_session, run_id=run.id)
    assert delivered == 1
    assert await AuditOutboxService.has_pending(db_session, run.id) is False


@pytest.mark.asyncio
async def test_audit_outbox_projection_is_idempotent(db_session: AsyncSession) -> None:
    """Retrying a delivered row cannot create a duplicate append-only projection."""
    user = User(email="idempotent-outbox@example.com")
    db_session.add(user)
    await db_session.flush()
    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_outbox_idempotent",
        user_id=user.id,
        request="test idempotent outbox",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        correlation_id="corr_outbox_idempotent",
    )
    pending = await AuditService.record_tool_execution(
        db_session,
        run_id=run.id,
        tool_name="idempotent.tool",
        input_parameters={"value": 1},
        success=True,
        latency_ms=1.0,
    )
    assert await AuditOutboxService.deliver_pending(db_session, run_id=run.id) == 1
    assert await AuditOutboxService.deliver_pending(db_session, run_id=run.id) == 0
    projections = list((await db_session.execute(select(ToolExecution))).scalars())
    assert len(projections) == 1
    assert projections[0].outbox_id == pending.id
    outbox = await db_session.scalar(select(AuditOutbox).where(AuditOutbox.id == pending.id))
    assert outbox is not None
    assert outbox.status == "delivered"


@pytest.mark.asyncio
async def test_waiting_approval_checkpoint_and_terminal_transition_rules(db_session: AsyncSession) -> None:
    """Verify pause checkpoints persist and terminal states can only be written through completion."""
    user = User(email="checkpoint_user@example.com")
    db_session.add(user)
    await db_session.flush()
    run = await RunPersistenceService.create_run(
        session=db_session,
        run_id="run_checkpoint_1",
        user_id=user.id,
        request="Pause for approval",
        route_type="direct_specialist",
        domains=["calendar"],
        complexity="direct",
        correlation_id="corr_checkpoint_1",
    )
    state = AssistantState(
        run_id=run.id,
        user_id=user.id,
        request="Pause for approval",
        status=RunStatus.WAITING_APPROVAL,
    )

    checkpoint = await RunPersistenceService.update_run_state(db_session, run.id, state)
    assert checkpoint is not None
    assert checkpoint.status == RunStatus.WAITING_APPROVAL.value
    assert checkpoint.state_snapshot is not None
    assert checkpoint.state_version >= 1

    terminal_state = state.model_copy(update={"status": RunStatus.COMPLETED})
    with pytest.raises(ValueError, match="complete_run"):
        await RunPersistenceService.update_run_state(db_session, run.id, terminal_state)

    resumed_state = state.model_copy(update={"status": RunStatus.RUNNING})
    resumed = await RunPersistenceService.update_run_state(db_session, run.id, resumed_state)
    assert resumed is not None
    assert resumed.status == RunStatus.RUNNING.value

    completed = await RunPersistenceService.complete_run(
        db_session,
        run.id,
        RunStatus.COMPLETED,
        total_latency_ms=10.0,
    )
    assert completed is not None
    assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_waiting_checkpoint_round_trips_full_continuation_context(
    db_session: AsyncSession,
) -> None:
    """Waiting checkpoints retain the data required to continue after a process restart."""
    user = User(email="roundtrip@example.com")
    db_session.add(user)
    await db_session.flush()
    run = await RunPersistenceService.create_run(
        session=db_session,
        run_id="run_roundtrip_1",
        user_id=user.id,
        request="Complete the task",
        route_type="supervisor",
        domains=["general"],
        complexity="multi_step",
        correlation_id="corr_roundtrip_1",
    )
    task = ExecutionTask(
        id="task_roundtrip_1",
        name="continue",
        assigned_agent="general",
        description="Continue after user input",
    )
    state = AssistantState(
        run_id=run.id,
        user_id=user.id,
        request=run.request,
        plan=ExecutionPlan(plan_id="plan_roundtrip_1", goal="Complete the task", tasks=[task]),
        task_results=[TaskResult(task_id=task.id, status=TaskStatus.PENDING)],
        missing_information=["recipient"],
        proposed_actions=[
            ProposedAction(
                id="action_roundtrip_1",
                action_type="send_email",
                description="Send the final message",
                parameters={"secret_token": "must-not-survive"},
            )
        ],
        continuation_context={"task_id": task.id},
        task_context={"active_node": task.id},
        status=RunStatus.WAITING_INPUT,
    )
    await RunPersistenceService.update_run_state(db_session, run.id, state)
    checkpoint_run_id = run.id
    await db_session.commit()
    db_session.expire_all()

    restored = await RunPersistenceService.load_state(db_session, checkpoint_run_id)
    assert restored is not None
    assert restored.status == RunStatus.WAITING_INPUT
    assert restored.missing_information == ["recipient"]
    assert restored.plan is not None
    assert restored.plan.plan_id == "plan_roundtrip_1"
    assert restored.task_results[0].task_id == task.id
    assert restored.proposed_actions[0].id == "action_roundtrip_1"
    assert restored.proposed_actions[0].parameters["secret_token"] == "[REDACTED_SECRET]"
    assert restored.continuation_context == {"task_id": task.id}
    assert restored.task_context == {"active_node": task.id}

    resumed = await RunPersistenceService.resume_waiting_input(
        db_session,
        checkpoint_run_id,
        {"recipient": "alice@example.com"},
    )
    assert resumed.status == RunStatus.RUNNING
    assert resumed.continuation_context["last_input"] == {"recipient": "a***@example.com"}


@pytest.mark.asyncio
async def test_terminal_completion_is_idempotent_and_version_conflicts_are_rejected(
    db_session: AsyncSession,
) -> None:
    """Retries do not rewrite terminal timestamps, while stale checkpoint versions fail."""
    user = User(email="idempotent@example.com")
    db_session.add(user)
    await db_session.flush()
    run = await RunPersistenceService.create_run(
        session=db_session,
        run_id="run_idempotent_1",
        user_id=user.id,
        request="Finish",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        correlation_id="corr_idempotent_1",
    )
    state = AssistantState(
        run_id=run.id,
        user_id=user.id,
        request="Finish",
        status=RunStatus.RUNNING,
    )
    await RunPersistenceService.update_run_state(db_session, run.id, state)
    stale_version = run.state_version - 1
    with pytest.raises(ValueError, match="Stale run version"):
        await RunPersistenceService.update_run_state(
            db_session,
            run.id,
            state,
            expected_version=stale_version,
        )

    completed = await RunPersistenceService.complete_run(
        db_session,
        run.id,
        RunStatus.COMPLETED,
        total_latency_ms=10.0,
    )
    assert completed is not None
    completed_at = completed.completed_at
    retried = await RunPersistenceService.complete_run(
        db_session,
        run.id,
        RunStatus.COMPLETED,
        total_latency_ms=999.0,
    )
    assert retried is not None
    assert retried is completed
    assert retried.completed_at == completed_at
    assert retried.total_latency_ms == 10.0
    restored = await RunPersistenceService.load_state(db_session, run.id)
    assert restored is not None
    assert restored.status == RunStatus.COMPLETED
