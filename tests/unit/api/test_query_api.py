"""Unit tests for POST /query."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user_id
from app.api.routes.query import get_query_orchestrator
from app.api.routes.query import router as query_router
from app.domain.models import (
    CalendarEvent,
    CalendarEventPage,
    CalendarEventTime,
    EmailAddress,
    GmailMessage,
    GmailMessagePage,
    GmailMessageSummary,
)
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import get_db_session
from app.services.retrieval.synthesis import SynthesisResult
from app.services.routing.query import QueryOrchestrator

NOW = datetime(2026, 9, 10, 15, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))


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


def _app(db_session: AsyncSession, orchestrator: QueryOrchestrator) -> FastAPI:
    app = FastAPI()
    app.include_router(query_router)
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_user_id] = lambda: "user_bob"
    app.dependency_overrides[get_query_orchestrator] = lambda: orchestrator
    return app


class _FakeCalendar:
    async def list_events(self, *args: object, **kwargs: object) -> CalendarEventPage:
        start = datetime(2026, 9, 11, 9, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        end = datetime(2026, 9, 11, 10, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        return CalendarEventPage(
            items=[
                CalendarEvent(
                    id="evt-1",
                    summary="Standup",
                    start=CalendarEventTime(value=start, time_zone="Asia/Ho_Chi_Minh"),
                    end=CalendarEventTime(value=end, time_zone="Asia/Ho_Chi_Minh"),
                )
            ]
        )


@pytest.mark.asyncio
async def test_query_calendar_list(db_session: AsyncSession) -> None:
    async def calendar_for_user(_session: AsyncSession, _user_id: str) -> _FakeCalendar:
        return _FakeCalendar()

    app = _app(
        db_session,
        QueryOrchestrator(calendar_for_user=calendar_for_user, clock=lambda: NOW),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/query", json={"query": "Lịch ngày mai?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["route"]["target_agent"] == "CalendarAgent"
    assert body["data"]["count"] == 1
    assert "Standup" in body["message"]


@pytest.mark.asyncio
async def test_query_knowledge(db_session: AsyncSession) -> None:
    async def retrieve(query: str, user_id: str) -> SynthesisResult:
        return SynthesisResult(
            answer=f"Tóm tắt cho {user_id}: hybrid retrieval.",
            status=SufficiencyStatus.SUFFICIENT,
        )

    app = _app(db_session, QueryOrchestrator(retrieve=retrieve))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/query",
            json={"query": "Tài liệu nội bộ nói gì về hybrid retrieval?"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["route"]["target_agent"] == "KnowledgeResearchAgent"
    assert "hybrid retrieval" in body["message"]


@pytest.mark.asyncio
async def test_query_booking_needs_approval(db_session: AsyncSession) -> None:
    app = _app(db_session, QueryOrchestrator(clock=lambda: NOW))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/query",
            json={"query": "Tạo lịch họp lúc 10h sáng mai"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_approval"
    assert body["approval_id"]


@pytest.mark.asyncio
async def test_query_rejects_blank(db_session: AsyncSession) -> None:
    app = _app(db_session, QueryOrchestrator())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/query", json={"query": "   "})
    assert response.status_code == 422


class _FakeGmail:
    async def search_messages(self, query: str = "", *, page_size: int = 100, **kwargs: object):
        del query, page_size, kwargs
        return GmailMessagePage(
            items=[GmailMessageSummary(id="m1", thread_id="t1", snippet="hello")]
        )

    async def get_message(self, message_id: str, *, format: str = "full") -> GmailMessage:
        del format
        return GmailMessage(
            id=message_id,
            thread_id="t1",
            snippet="Invoice attached",
            subject="Invoice",
            sender=EmailAddress(email="billing@example.com", display_name="Billing"),
        )


@pytest.mark.asyncio
async def test_query_reads_gmail(db_session: AsyncSession) -> None:
    async def communication_for_user(_session: AsyncSession, _user_id: str) -> _FakeGmail:
        return _FakeGmail()

    app = _app(
        db_session,
        QueryOrchestrator(communication_for_user=communication_for_user),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/query", json={"query": "Đọc email mới nhất"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["route"]["target_agent"] == "CommunicationAgent"
    assert body["data"]["count"] == 1
    assert "Invoice" in body["message"]


@pytest.mark.asyncio
async def test_query_supervisor_dag_execution(db_session: AsyncSession) -> None:
    async def retrieve(query: str, user_id: str) -> SynthesisResult:
        return SynthesisResult(
            answer="Quy chế chi tiêu quy định hạn mức công tác phí tối đa 1.000.000 VNĐ/ngày.",
            status=SufficiencyStatus.SUFFICIENT,
        )

    app = _app(db_session, QueryOrchestrator(retrieve=retrieve, clock=lambda: NOW))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/query",
            json={"query": "tìm quy chế chi tiêu và xem lịch để soạn email báo cáo"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_approval"
    assert body["approval_id"] is not None
    assert body["route"]["route_type"] == "supervisor_dag"
    assert "Supervisor DAG" in body["message"]
    assert "quy định hạn mức" in body["message"]
    assert "Bản nháp email" in body["message"]

