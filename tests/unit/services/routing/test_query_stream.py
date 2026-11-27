"""The SSE path finalises runs honestly and mirrors the JSON path's handlers and data."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.declarations import CALENDAR_AGENT_NAME
from app.domain.enums import Complexity, Domain, RouteType, RunStatus
from app.domain.models.routing.route import RouteDecision
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import AssistantRun, User
from app.integrations.google_tasks import TaskItem
from app.services.routing.query import QueryOrchestrator, QueryResult, QueryRouteInfo

NOW = datetime(2026, 10, 27, 2, 0, tzinfo=UTC)


class FakeTasks:
    async def list_tasks(self, **_kwargs: Any) -> list[TaskItem]:
        return [TaskItem(id="t1", title="Gọi anh Nam", due=date(2026, 10, 25))]


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(User(id="user_a", email="a@x.vn"))
        await session.flush()
        yield session
    await engine.dispose()


def _orchestrator() -> QueryOrchestrator:
    async def tasks_for_user(_session: AsyncSession, _user_id: str) -> FakeTasks:
        return FakeTasks()

    return QueryOrchestrator(tasks_for_user=tasks_for_user, clock=lambda: NOW)


def _calendar_decision() -> RouteDecision:
    return RouteDecision(
        route_type=RouteType.DIRECT_SPECIALIST,
        target_agent=CALENDAR_AGENT_NAME,
        confidence=0.9,
        domains=[Domain.CALENDAR],
        complexity=Complexity.DIRECT,
    )


async def _only_run(session: AsyncSession) -> AssistantRun:
    runs = (await session.execute(AssistantRun.__table__.select())).all()
    assert len(runs) == 1
    run = await session.get(AssistantRun, runs[0].id)
    assert run is not None
    return run


async def test_client_disconnect_records_the_run_cancelled(db_session: AsyncSession) -> None:
    stream = _orchestrator().handle_stream(db_session, "user_a", "việc cần làm hôm nay?")
    first = await anext(stream)
    assert first.startswith("event: metadata")
    await stream.aclose()
    assert (await _only_run(db_session)).status == RunStatus.CANCELLED.value


async def test_failure_stores_a_generic_summary_and_logs(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    orchestrator = _orchestrator()

    async def boom(*_args: Any, **_kwargs: Any) -> QueryResult | None:
        raise RuntimeError("secret connection string leaked")

    orchestrator._handle_command = boom  # type: ignore[method-assign]
    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError):
        async for _ in orchestrator.handle_stream(db_session, "user_a", "xin chào"):
            pass
    run = await _only_run(db_session)
    assert run.status == RunStatus.FAILED.value
    assert run.error_summary == "Query execution failed."
    assert "stream_query_failed" in caplog.text


async def test_command_results_send_their_data_like_the_json_path(
    db_session: AsyncSession,
) -> None:
    orchestrator = _orchestrator()
    json_result = await orchestrator.handle(db_session, "user_a", "việc cần làm hôm nay?")
    assert json_result.data
    chunks = [
        chunk
        async for chunk in orchestrator.handle_stream(db_session, "user_a", "việc cần làm hôm nay?")
    ]
    assert any(chunk.startswith("event: citations") and '"data"' in chunk for chunk in chunks)
    assert '"status": "completed"' in chunks[-1]


@pytest.mark.parametrize("streaming", [False, True])
async def test_routed_handlers_get_the_rewritten_follow_up(
    db_session: AsyncSession, streaming: bool
) -> None:
    orchestrator = _orchestrator()
    seen: list[str] = []

    async def triage(query: str, _history: Any) -> tuple[str, RouteDecision, None]:
        return "lịch họp ngày mai", _calendar_decision(), None

    async def calendar(
        _session: AsyncSession, _user: str, query: str, run_id: str, route: QueryRouteInfo
    ) -> QueryResult:
        seen.append(query)
        return QueryResult(run_id=run_id, status="completed", message="ok", route=route)

    orchestrator._triage_in_context = triage  # type: ignore[method-assign]
    orchestrator._handle_calendar = calendar  # type: ignore[method-assign]
    if streaming:
        async for _ in orchestrator.handle_stream(db_session, "user_a", "còn ngày mai?"):
            pass
    else:
        await orchestrator.handle(db_session, "user_a", "còn ngày mai?")
    assert seen == ["lịch họp ngày mai"]


async def test_ensure_user_creates_a_placeholder_once(db_session: AsyncSession) -> None:
    await QueryOrchestrator.ensure_user(db_session, "user_b")
    await QueryOrchestrator.ensure_user(db_session, "user_b")
    user = await db_session.get(User, "user_b")
    assert user is not None and user.email == "user_b@users.local"
