"""Unit tests for goals and watch rules: commands, planning, chat flow and worker jobs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings.proactive import ProactiveSettings
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import Document, Goal, User, WatchRule
from app.services.goals import planner as planner_module
from app.services.goals.commands import GoalCommand, parse_goal_command
from app.services.goals.planner import fallback_plan, plan_steps
from app.services.proactive.goals import GoalReminderJob
from app.services.proactive.watcher import WatcherJob
from app.services.routing.query import QueryOrchestrator
from tests.unit.services.proactive._fakes import FakeMail, FakeSources, make_mail

TODAY = date(2026, 10, 27)
NOW = datetime(2026, 10, 27, 2, 0, tzinfo=UTC)  # 09:00 in Hanoi


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
    ("query", "expected"),
    [
        (
            "Đặt mục tiêu: chuẩn bị hội thảo AI trước 15/12",
            GoalCommand("goal_create", title="chuẩn bị hội thảo AI", due=date(2026, 12, 15)),
        ),
        ("tiến độ mục tiêu", GoalCommand("goal_progress")),
        (
            "xong bước 2 của mục tiêu hội thảo",
            GoalCommand("goal_step_done", title="hội thảo", step=2),
        ),
        (
            "Báo tôi khi nam@atin.vn trả lời",
            GoalCommand("watch_create", watch_kind="mail_from", target="nam@atin.vn"),
        ),
        (
            "theo dõi email có «hợp đồng»",
            GoalCommand("watch_create", watch_kind="mail_keyword", target="hợp đồng"),
        ),
        (
            "báo cho tôi khi có tài liệu mới về quy chế chi tiêu",
            GoalCommand("watch_create", watch_kind="document_topic", target="quy chế chi tiêu"),
        ),
        ("Đang theo dõi gì?", GoalCommand("watch_list")),
        ("dừng theo dõi nam@atin.vn", GoalCommand("watch_stop", target="nam@atin.vn")),
        ("Theo dõi lịch họp", None),
        ("việc cần làm?", None),
    ],
)
def test_parse_goal_command(query: str, expected: GoalCommand | None) -> None:
    assert parse_goal_command(query, TODAY) == expected


async def test_planner_clamps_model_steps_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def model(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "steps": [
                {"title": "Chốt diễn giả", "due": "2026-10-01"},
                {"title": "Gửi thư mời", "due": "2027-03-01"},
                {"title": "", "due": "2026-11-01"},
                {"title": "Đặt phòng", "due": "không rõ"},
            ]
        }

    monkeypatch.setattr(planner_module, "llm_json", model)
    steps = await plan_steps("Hội thảo", TODAY, date(2026, 12, 15))
    assert [(s["title"], s["due"]) for s in steps] == [
        ("Chốt diễn giả", "2026-10-27"),
        ("Gửi thư mời", "2026-12-15"),
        ("Đặt phòng", "2026-12-15"),
    ]

    async def no_model(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(planner_module, "llm_json", no_model)
    fallback = await plan_steps("Hội thảo", TODAY, None)
    assert len(fallback) == 4 and fallback[-1]["due"] == "2026-11-10"
    assert fallback_plan(TODAY, date(2026, 10, 1))[-1]["due"] == "2026-11-10"


async def _planner(_title: str, today: date, _due: date | None) -> list[dict[str, Any]]:
    return [
        {"title": "Bước một", "due": (today - timedelta(days=1)).isoformat(), "done": False},
        {"title": "Bước hai", "due": today.isoformat(), "done": False},
        {"title": "Bước ba", "due": (today + timedelta(days=5)).isoformat(), "done": False},
    ]


async def test_goal_chat_flow(db_session: AsyncSession) -> None:
    orchestrator = QueryOrchestrator(goal_planner=_planner, clock=lambda: NOW)

    async def ask(text: str) -> str:
        return (await orchestrator.handle(db_session, "user_a", text)).message

    created = await ask("Đặt mục tiêu: chuẩn bị hội thảo AI trước 15/12")
    assert "Đã đặt mục tiêu «chuẩn bị hội thảo AI» (hạn 15/12/2026)" in created
    assert "1. Bước một — hạn 26/10" in created

    progress = await ask("tiến độ mục tiêu")
    assert "**chuẩn bị hội thảo AI** — 0/3 bước" in progress and "⚠️ 1. Bước một" in progress

    assert "Đã đánh dấu xong bước 1" in await ask("xong bước 1")
    assert "không có bước 9" in await ask("xong bước 9 của mục tiêu hội thảo")
    await ask("xong bước 2")
    finished = await ask("xong bước 3")
    assert finished.endswith("Mục tiêu đã hoàn thành!")
    assert "chưa có mục tiêu" in await ask("tiến độ mục tiêu")


async def test_watch_chat_flow(db_session: AsyncSession) -> None:
    orchestrator = QueryOrchestrator(clock=lambda: NOW)

    async def ask(text: str, user: str = "user_a") -> str:
        return (await orchestrator.handle(db_session, user, text)).message

    assert "Đã bật theo dõi email từ «nam@atin.vn»" in await ask("báo tôi khi nam@atin.vn trả lời")
    await ask("báo tôi khi nam@atin.vn trả lời")
    await ask("theo dõi email có «hợp đồng»")
    listing = await ask("đang theo dõi gì?")
    assert listing.count("«nam@atin.vn»") == 1 and "«hợp đồng»" in listing
    assert "chưa theo dõi" in await ask("đang theo dõi gì?", user="user_b")
    assert "Đã dừng theo dõi: «nam@atin.vn»" in await ask("dừng theo dõi nam@atin.vn")
    assert "Không có mục theo dõi nào khớp" in await ask("dừng theo dõi lan@x.vn")


async def test_watcher_reports_new_mail_and_documents_once(db_session: AsyncSession) -> None:
    db_session.add_all(
        [
            WatchRule(id="w-mail", user_id="user_a", kind="mail_from", target="nam@x.vn", last_checked_at=NOW - timedelta(hours=1)),
            WatchRule(id="w-doc", user_id="user_a", kind="document_topic", target="quy chế chi tiêu", last_checked_at=NOW - timedelta(hours=1)),
            WatchRule(id="w-old", user_id="user_a", kind="mail_keyword", target="x", expires_at=NOW - timedelta(minutes=1)),
            Document(id="d1", logical_document_id="L1", source_type="file", title="Quy chế chi tiêu nội bộ 2026", is_active=True, created_at=NOW - timedelta(minutes=10)),
            Document(id="d2", logical_document_id="L2", source_type="file", title="Quy chế chi tiêu cũ", is_active=True, created_at=NOW - timedelta(days=3)),
            Document(id="d3", logical_document_id="L3", source_type="file", title="Quy chế chi tiêu (riêng)", is_active=True, user_id="user_b", created_at=NOW - timedelta(minutes=5)),
        ]
    )  # fmt: skip
    await db_session.flush()
    mail = FakeMail(
        messages=[
            make_mail("new", NOW - timedelta(minutes=20)),
            make_mail("before", NOW - timedelta(hours=3)),
        ]
    )
    job = WatcherJob(FakeSources(mail=mail), ProactiveSettings(mail_watch_interval_minutes=5))

    created = await job.run(db_session, "user_a", NOW)
    assert sorted(n.dedup_key for n in created) == ["watch:w-doc:L1", "watch:w-mail:new"]
    assert mail.queries == ["from:nam@x.vn newer_than:2d"]
    assert (await db_session.get(WatchRule, "w-old")).active is False  # type: ignore[union-attr]
    assert await job.run(db_session, "user_a", NOW + timedelta(minutes=1)) == []


async def test_goal_reminder_lists_due_steps_once_a_day(db_session: AsyncSession) -> None:
    steps = await _planner("x", TODAY, None)
    db_session.add(Goal(id="g1", user_id="user_a", title="Hội thảo", steps=steps))
    await db_session.flush()
    job = GoalReminderJob(ProactiveSettings())

    assert await job.run(db_session, "user_a", datetime(2026, 10, 26, 23, 0, tzinfo=UTC)) == []
    created = await job.run(db_session, "user_a", NOW)
    assert len(created) == 1
    body = created[0].body
    assert "- Bước 1: Bước một — quá hạn" in body and "- Bước 2: Bước hai" in body
    assert "Bước ba" not in body
    assert await job.run(db_session, "user_a", NOW + timedelta(hours=1)) == []
