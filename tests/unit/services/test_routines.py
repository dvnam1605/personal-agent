"""Unit tests for user-defined routines: schedule parsing, storage, chat and the job."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings.proactive import ProactiveSettings
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import Routine, User
from app.services.proactive.routines import RoutineJob
from app.services.routines.commands import (
    ALL_DAYS,
    WORKDAYS,
    RoutineCommand,
    describe,
    parse_routine_command,
    parse_schedule,
)
from app.services.routines.service import RoutineError, RoutineService
from app.services.routing.query import QueryOrchestrator
from tests.unit.services.proactive._fakes import FakeSources

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all([User(id="user_a", email="a@x.vn"), User(id="user_b", email="b@x.vn")])
        await session.flush()
        yield session
    await engine.dispose()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mỗi thứ 6 lúc 17h", ((4,), time(17, 0))),
        ("hằng ngày 8:30", (ALL_DAYS, time(8, 30))),
        ("ngày làm việc 7h sáng", (WORKDAYS, time(7, 0))),
        ("thứ 2, thứ 5 lúc 5h chiều", ((0, 3), time(17, 0))),
        ("chủ nhật 20h", ((6,), time(20, 0))),
        ("mỗi tuần", None),
        ("25h", None),
    ],
)
def test_parse_schedule(text: str, expected: tuple[tuple[int, ...], time] | None) -> None:
    assert parse_schedule(text) == expected


def test_parse_commands_and_describe() -> None:
    assert parse_routine_command("Tạo quy trình hằng ngày 8:30: việc cần làm?") == RoutineCommand(
        "create", query="việc cần làm?", weekdays=ALL_DAYS, run_time=time(8, 30)
    )
    assert parse_routine_command("Các quy trình của tôi") == RoutineCommand("list")
    assert parse_routine_command("xóa quy trình 2") == RoutineCommand("delete", target="2")
    assert parse_routine_command("Lịch ngày mai?") is None
    assert describe(WORKDAYS, time(7, 30)) == "thứ 2–6 lúc 07:30"
    assert describe((4, 6), time(17, 0)) == "thứ 6, chủ nhật lúc 17:00"


async def test_storage_guards_and_due_rule(db_session: AsyncSession) -> None:
    with pytest.raises(RoutineError):
        await RoutineService.create(db_session, "user_a", "nhớ rằng x", ALL_DAYS, time(8))
    with pytest.raises(RoutineError):
        await RoutineService.create(
            db_session, "user_a", "tạo quy trình hằng ngày 8h: x", ALL_DAYS, time(8)
        )
    routine = await RoutineService.create(db_session, "user_a", "Lịch hôm nay?", WORKDAYS, time(8))

    tuesday_8 = datetime(2026, 10, 27, 8, 5, tzinfo=ZONE)
    assert RoutineService.is_due(routine, tuesday_8)
    assert not RoutineService.is_due(routine, tuesday_8.replace(hour=7))
    assert not RoutineService.is_due(routine, tuesday_8.replace(hour=11))
    assert not RoutineService.is_due(routine, datetime(2026, 11, 1, 8, 5, tzinfo=ZONE))
    routine.last_run_on = date(2026, 10, 27)
    assert not RoutineService.is_due(routine, tuesday_8)

    for index in range(9):
        await RoutineService.create(db_session, "user_a", f"việc {index}", ALL_DAYS, time(9))
    with pytest.raises(RoutineError):
        await RoutineService.create(db_session, "user_a", "quá nhiều", ALL_DAYS, time(9))
    assert await RoutineService.active(db_session, "user_b") == []


async def test_chat_flow(db_session: AsyncSession) -> None:
    orchestrator = QueryOrchestrator()

    async def ask(text: str) -> str:
        return (await orchestrator.handle(db_session, "user_a", text)).message

    created = await ask("Tạo quy trình mỗi thứ 6 lúc 17h: tổng hợp email tuần này")
    assert created.startswith("Đã tạo quy trình “tổng hợp email tuần này” — thứ 6 lúc 17:00")
    await ask("tạo quy trình hằng ngày 8:30: việc cần làm?")
    listing = await ask("các quy trình của tôi")
    assert "1. tổng hợp email tuần này — thứ 6 lúc 17:00" in listing
    assert "2. việc cần làm? — hằng ngày lúc 08:30" in listing
    assert "Đã tắt: “việc cần làm?”" in await ask("xóa quy trình 2")
    assert "Đã tắt: “tổng hợp email tuần này”" in await ask("tắt quy trình tổng hợp email")
    assert "chưa có quy trình" in await ask("các quy trình của tôi")
    assert "không được tự tạo" in await ask("tạo quy trình hằng ngày 8h: nhớ rằng x")


async def test_job_runs_due_routines_once_through_the_orchestrator(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        Routine(
            id="r1",
            user_id="user_a",
            name="Lịch",
            query="Lịch hôm nay?",
            weekdays="1",
            run_time=time(8),
        )
    )
    await db_session.flush()
    sources = FakeSources()
    sources.ask_result = SimpleNamespace(message="Hôm nay có 2 cuộc họp.", approval_id="ap-1")
    job = RoutineJob(sources, ProactiveSettings())
    now = datetime(2026, 10, 27, 1, 10, tzinfo=UTC)  # 08:10 Tuesday in Hanoi

    created = await job.run(db_session, "user_a", now)
    assert sources.asked == ["Lịch hôm nay?"]
    assert len(created) == 1 and created[0].title == "Quy trình: Lịch"
    assert created[0].body.endswith("Có hành động cần bạn duyệt trong ứng dụng hoặc Telegram.")
    assert await job.run(db_session, "user_a", now.replace(minute=30)) == []
    assert sources.asked == ["Lịch hôm nay?"]
