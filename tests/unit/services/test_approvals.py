"""Unit tests for durable approval lifecycle and continuation."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.enums import RunStatus
from app.domain.models import AssistantState, ProposedAction
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.services.approvals import ApprovalRequestService
from app.services.run_persistence import RunPersistenceService


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Create an isolated SQLite session for approval lifecycle tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_approval_request_create_decide_and_resume(db_session: AsyncSession) -> None:
    """An approved action survives the pause and rehydrates into the run state."""
    user = User(email="approval@example.com")
    db_session.add(user)
    await db_session.flush()
    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_approval_1",
        user_id=user.id,
        request="Send a message",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_approval_1",
    )
    action = ProposedAction(
        id="action_approval_1",
        action_type="send_email",
        description="Send the message",
        tool_name="gmail.send",
        parameters={"to": "alice@example.com", "api_key": "sk-secret"},
    )
    state = AssistantState(
        run_id=run.id,
        user_id=user.id,
        request=run.request,
        proposed_actions=[action],
        status=RunStatus.RUNNING,
    )

    request = await ApprovalRequestService.create_request(
        db_session,
        run.id,
        action,
        state=state,
    )
    assert request.status == "pending"
    assert request.parameters["api_key"] == "[REDACTED_SECRET]"
    assert run.status == RunStatus.WAITING_APPROVAL.value

    duplicate = await ApprovalRequestService.create_request(
        db_session,
        run.id,
        action,
        state=state,
    )
    assert duplicate.id == request.id

    decided = await ApprovalRequestService.decide(
        db_session,
        request.id,
        approved=True,
        approver_id=user.id,
        reason="Looks good",
    )
    assert decided.status == "approved"
    retry = await ApprovalRequestService.decide(
        db_session,
        request.id,
        approved=True,
        approver_id=user.id,
    )
    assert retry.id == request.id

    resumed = await ApprovalRequestService.resume_approved(db_session, request.id)
    assert resumed.status == RunStatus.RUNNING
    assert resumed.approvals[0].action_id == action.id
    assert resumed.continuation_context["approved_request_id"] == request.id


@pytest.mark.asyncio
async def test_expired_approval_is_not_resumable(db_session: AsyncSession) -> None:
    """Expired requests are transitioned once and cannot be approved or resumed."""
    user = User(email="expired-approval@example.com")
    db_session.add(user)
    await db_session.flush()
    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_approval_expired",
        user_id=user.id,
        request="Delete an item",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        correlation_id="corr_approval_expired",
    )
    action = ProposedAction(
        id="action_approval_expired",
        action_type="delete_item",
        description="Delete the item",
    )
    request = await ApprovalRequestService.create_request(
        db_session,
        run.id,
        action,
        expires_in_seconds=1,
    )
    future = datetime.now(UTC) + timedelta(seconds=2)
    with pytest.raises(ValueError, match="expired"):
        await ApprovalRequestService.decide(
            db_session,
            request.id,
            approved=True,
            approver_id=user.id,
            now=future,
        )
    assert request.status == "expired"
    with pytest.raises(ValueError, match="not approved"):
        await ApprovalRequestService.resume_approved(db_session, request.id)


@pytest.mark.asyncio
async def test_expire_pending_transitions_requests_in_batch(db_session: AsyncSession) -> None:
    """The operational expiry method transitions pending rows without a decision call."""
    user = User(email="batch-expiry@example.com")
    db_session.add(user)
    await db_session.flush()
    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_approval_batch_expiry",
        user_id=user.id,
        request="Approve an action",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        correlation_id="corr_approval_batch_expiry",
    )
    request = await ApprovalRequestService.create_request(
        db_session,
        run.id,
        ProposedAction(
            id="action_approval_batch_expiry",
            action_type="write_item",
            description="Write the item",
        ),
        expires_in_seconds=1,
    )
    expired = await ApprovalRequestService.expire_pending(
        db_session,
        now=datetime.now(UTC) + timedelta(seconds=2),
    )
    assert expired == 1
    assert request.status == "expired"
    assert request.approved is False
