"""Unit tests for questions REST API endpoints (spec P18-04)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user_id
from app.api.routes.questions import router as questions_router
from app.domain.models import UserQuestionItem, UserQuestionOption
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_db_session
from app.services.approvals.question_plane import QuestionPlaneService
from app.services.platform.run_persistence import RunPersistenceService


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
    app.include_router(questions_router)

    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_user_id] = lambda: "user_bob"
    return app


@pytest.mark.asyncio
async def test_answer_question_api_success(app: FastAPI, db_session: AsyncSession) -> None:
    questions = [
        UserQuestionItem(
            id="item_1",
            question="Select meeting length",
            options=[
                UserQuestionOption(label="30 minutes"),
                UserQuestionOption(label="60 minutes"),
            ],
            multi_select=False,
        )
    ]
    user = User(id="user_bob", email="bob@example.com")
    db_session.add(user)
    await db_session.flush()

    await RunPersistenceService.create_run(
        db_session,
        run_id="run_q_1",
        user_id="user_bob",
        request="Test question 1",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        correlation_id="corr_q_1",
    )

    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id="run_q_1",
        questions=questions,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/questions/{created.id}/answer",
            json={
                "answers": [{"question_id": "item_1", "selected_options": ["30 minutes"]}],
                "expected_version": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["question_id"] == created.id
        assert data["status"] == "answered"
        assert data["answered_by"] == "user_bob"
        assert data["answers"][0]["selected_options"] == ["30 minutes"]
        assert data["version"] == 2


@pytest.mark.asyncio
async def test_answer_question_api_invalid_option_fails(
    app: FastAPI, db_session: AsyncSession
) -> None:
    questions = [
        UserQuestionItem(
            id="item_2",
            question="Confirm participation?",
            options=[
                UserQuestionOption(label="Yes"),
                UserQuestionOption(label="No"),
            ],
            multi_select=False,
        )
    ]
    await RunPersistenceService.create_run(
        db_session,
        run_id="run_q_2",
        user_id="user_bob",
        request="Test question 2",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        correlation_id="corr_q_2",
    )

    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id="run_q_2",
        questions=questions,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/questions/{created.id}/answer",
            json={
                "answers": [{"question_id": "item_2", "selected_options": ["Maybe"]}],
                "expected_version": 1,
            },
        )
        assert response.status_code == 400
        assert "not valid for question" in response.json()["detail"]


@pytest.mark.asyncio
async def test_cross_user_answer_question_forbidden(app: FastAPI, db_session: AsyncSession) -> None:
    """Submitting answers to a question belonging to another user raises 403 Forbidden (M4)."""
    from app.infrastructure.db.models import User
    from app.services.platform.run_persistence import RunPersistenceService

    alice = User(id="user_alice", email="alice@example.com")
    db_session.add(alice)
    await db_session.flush()

    run = await RunPersistenceService.create_run(
        db_session,
        run_id="run_alice_q",
        user_id=alice.id,
        request="Alice request",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
        correlation_id="corr_alice_q",
    )

    questions = [
        UserQuestionItem(
            id="q_alice_1",
            question="Alice question?",
            options=[UserQuestionOption(label="Yes"), UserQuestionOption(label="No")],
            multi_select=False,
        )
    ]
    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id=run.id,
        questions=questions,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # app has caller user_bob, but question run belongs to user_alice
        response = await client.post(
            f"/questions/{created.id}/answer",
            json={
                "answers": [{"question_id": "q_alice_1", "selected_options": ["Yes"]}],
                "expected_version": 1,
            },
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_orphan_question_without_run_forbidden(
    app: FastAPI, db_session: AsyncSession
) -> None:
    questions = [
        UserQuestionItem(
            id="q_orphan",
            question="Orphan?",
            options=[UserQuestionOption(label="Yes"), UserQuestionOption(label="No")],
            multi_select=False,
        )
    ]
    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id=None,
        questions=questions,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/questions/{created.id}/answer",
            json={
                "answers": [{"question_id": "q_orphan", "selected_options": ["Yes"]}],
                "expected_version": 1,
            },
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_answer_question_version_conflict(app: FastAPI, db_session: AsyncSession) -> None:
    questions = [
        UserQuestionItem(
            id="item_v",
            question="Pick one",
            options=[UserQuestionOption(label="A"), UserQuestionOption(label="B")],
        )
    ]
    user = User(id="user_bob", email="bob@example.com")
    db_session.add(user)
    await db_session.flush()
    await RunPersistenceService.create_run(
        db_session,
        run_id="run_q_ver",
        user_id="user_bob",
        request="version",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        correlation_id="corr_q_ver",
    )
    created = await QuestionPlaneService.create_question_request(
        db_session, run_id="run_q_ver", questions=questions
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        stale = await client.post(
            f"/questions/{created.id}/answer",
            json={
                "answers": [{"question_id": "item_v", "selected_options": ["A"]}],
                "expected_version": 99,
            },
        )
        assert stale.status_code == 409
        ok = await client.post(
            f"/questions/{created.id}/answer",
            json={
                "answers": [{"question_id": "item_v", "selected_options": ["A"]}],
                "expected_version": 1,
            },
        )
        assert ok.status_code == 200
        assert ok.json()["version"] == 2
