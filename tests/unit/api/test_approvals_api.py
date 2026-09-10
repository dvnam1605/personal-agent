"""Unit tests for approvals REST API endpoints (spec P18-04)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user_id
from app.api.routes.approvals import router as approvals_router
from app.domain.enums import ActionRiskLevel
from app.domain.models import ProposedAction
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_db_session
from app.services.approvals import ApprovalRequestService
from app.services.run_persistence import RunPersistenceService


@pytest.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.fixture
def app(db_session: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.include_router(approvals_router)

    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_user_id] = lambda: "test-user-123"
    return app


@pytest.mark.asyncio
async def test_list_pending_approvals_api(app: FastAPI, db_session: AsyncSession) -> None:
    user = User(id="test-user-123", email="approver@example.com")
    db_session.add(user)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_api_1",
        user_id=user.id,
        request="Test request",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_api_1",
    )

    action = ProposedAction(
        action_type="send_email",
        description="Send message to customer",
        target="customer@example.com",
        tool_name="gmail.send",
        parameters={"to": "customer@example.com", "subject": "Hi"},
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )

    req = await ApprovalRequestService.create_request(db_session, run.id, action)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/approvals/pending")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(item["id"] == req.id for item in data)


@pytest.mark.asyncio
async def test_approve_mutation_api(app: FastAPI, db_session: AsyncSession) -> None:
    user = User(id="test-user-123", email="approver@example.com")
    db_session.add(user)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_api_2",
        user_id=user.id,
        request="Test request",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_api_2",
    )

    action = ProposedAction(
        action_type="send_email",
        description="Send email",
        tool_name="gmail.send",
        parameters={"to": "client@example.com"},
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    req = await ApprovalRequestService.create_request(db_session, run.id, action)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/approvals/{req.id}/approve",
            json={"reason": "Verified recipient"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["approval_id"] == req.id
        assert data["status"] == "approved"
        assert data["approved"] is True
        assert data["token"] is not None
        assert data["token"].startswith("appr_")


@pytest.mark.asyncio
async def test_deny_mutation_api(app: FastAPI, db_session: AsyncSession) -> None:
    user = User(id="test-user-123", email="approver@example.com")
    db_session.add(user)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_api_3",
        user_id=user.id,
        request="Test request",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_api_3",
    )

    action = ProposedAction(
        action_type="delete_event",
        description="Delete important event",
        tool_name="calendar.delete_event",
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        requires_approval=True,
    )
    req = await ApprovalRequestService.create_request(db_session, run.id, action)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/approvals/{req.id}/deny",
            json={"reason": "Do not delete", "outcome": "rejected"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["approval_id"] == req.id
        assert data["status"] == "rejected"
        assert data["approved"] is False
        assert data["reason"] == "Do not delete"


@pytest.mark.asyncio
async def test_cross_user_approval_forbidden(app: FastAPI, db_session: AsyncSession) -> None:
    """Approving an action belonging to another user raises 403 Forbidden (M4)."""
    user = User(id="other-user-999", email="other@example.com")
    db_session.add(user)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_other_user",
        user_id=user.id,
        request="Other request",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_other_user",
    )

    action = ProposedAction(
        action_type="delete_event",
        description="Delete event",
        tool_name="calendar.delete_event",
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        requires_approval=True,
    )
    req = await ApprovalRequestService.create_request(db_session, run.id, action)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # app has user_id="test-user-123", but approval belongs to "other-user-999"
        res_approve = await client.post(f"/approvals/{req.id}/approve")
        assert res_approve.status_code == 403

        res_deny = await client.post(f"/approvals/{req.id}/deny")
        assert res_deny.status_code == 403


@pytest.mark.asyncio
async def test_approve_idempotent_does_not_remint(app: FastAPI, db_session: AsyncSession) -> None:
    """Repeated approve requests do not issue a second execution token (N4)."""
    user = User(id="test-user-123", email="approver@example.com")
    db_session.add(user)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_idemp_token",
        user_id=user.id,
        request="Test token persistence",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_idemp_token",
    )

    action = ProposedAction(
        action_type="send_email",
        description="Send message",
        tool_name="gmail.send",
        parameters={"to": "client@example.com", "body": "Hi"},
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    req = await ApprovalRequestService.create_request(db_session, run.id, action)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res1 = await client.post(f"/approvals/{req.id}/approve")
        assert res1.status_code == 200
        token1 = res1.json()["token"]
        assert token1 is not None

        res2 = await client.post(f"/approvals/{req.id}/approve")
        assert res2.status_code == 409
        assert "already issued" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_list_pending_approvals_is_scoped_to_current_user(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """GET /approvals/pending must not leak another user's requests (M4)."""
    mine = User(id="test-user-123", email="me@example.com")
    other = User(id="other-user-999", email="other@example.com")
    db_session.add_all([mine, other])
    await db_session.flush()

    my_run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_list_mine",
        user_id=mine.id,
        request="Mine",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_list_mine",
    )
    other_run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_list_other",
        user_id=other.id,
        request="Other",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_list_other",
    )

    action = ProposedAction(
        action_type="send_email",
        description="Send",
        tool_name="gmail.send",
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )
    mine_req = await ApprovalRequestService.create_request(db_session, my_run.id, action)
    other_req = await ApprovalRequestService.create_request(db_session, other_run.id, action)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/approvals/pending")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()}
        assert mine_req.id in ids
        assert other_req.id not in ids
