"""Unit tests for loop → graph hardening candidates."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.db.base import Base
from app.infrastructure.db.models import AssistantRun, ToolExecution, User
from app.services.skills.hardening import find_candidates

T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(User(id="u", email="u@x.vn"))
        await session.flush()
        yield session
    await engine.dispose()


def _run(
    session: AsyncSession, run_id: str, skill: str, tools: list[str], status: str = "completed"
) -> None:
    session.add(
        AssistantRun(
            id=run_id,
            user_id="u",
            correlation_id=run_id,
            request="r",
            route_type="direct_specialist",
            domains=[],
            complexity="direct",
            status=status,
            active_skill=skill,
            total_latency_ms=1000.0,
            llm_call_count=3,
            completed_at=T0 if status in ("completed", "failed") else None,
        )
    )
    for index, tool in enumerate(tools):
        session.add(
            ToolExecution(
                run_id=run_id,
                tool_name=tool,
                input_parameters={},
                success=True,
                latency_ms=1.0,
                executed_at=T0 + timedelta(seconds=index),
            )
        )


async def test_only_frequent_stable_multi_step_routes_are_proposed(
    db_session: AsyncSession,
) -> None:
    for i in range(10):
        path = ["calendar.list_events", "gmail.search"] if i < 9 else ["calendar.list_events"]
        _run(db_session, f"stable-{i}", "CalendarAgent", path)
    for i in range(10):
        _run(db_session, f"noisy-{i}", "KnowledgeResearchAgent", ["knowledge.search"] * (i % 3 + 1))
    for i in range(3):
        _run(db_session, f"rare-{i}", "CommunicationAgent", ["gmail.search", "gmail.get"])
    for i in range(10):
        _run(db_session, f"single-{i}", "TaskAgent", ["tasks.list"])
    _run(db_session, "failed", "CalendarAgent", ["x"], status="failed")
    await db_session.flush()

    candidates = await find_candidates(db_session, min_runs=5, stability=0.9)
    assert [c.route for c in candidates] == ["direct_specialist:CalendarAgent"]
    best = candidates[0]
    assert best.runs == 10 and best.share == 0.9
    assert best.path == ("calendar.list_events", "gmail.search")
    assert best.avg_latency_ms == 1000.0 and best.avg_llm_calls == 3.0

    assert await find_candidates(db_session, min_runs=50) == []
