"""Unit tests for the morning briefing job."""

from __future__ import annotations

from datetime import date, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.domain.errors import NotFoundError
from app.integrations.google_tasks import TaskItem
from app.services.proactive import MorningBriefingJob, NotificationService
from tests.unit.services.proactive._fakes import (
    FakeCalendar,
    FakeMail,
    FakeSources,
    local,
    make_event,
)

SETTINGS = ProactiveSettings(briefing_time=time(7, 30), briefing_catch_up_hours=4)


def _job(sources: FakeSources, settings: ProactiveSettings = SETTINGS) -> MorningBriefingJob:
    return MorningBriefingJob(sources, settings)


def test_is_due_only_inside_the_briefing_window() -> None:
    job = _job(FakeSources())
    assert not job.is_due(local(2026, 10, 27, 7, 29))
    assert job.is_due(local(2026, 10, 27, 7, 30))
    assert job.is_due(local(2026, 10, 27, 11, 29))
    assert not job.is_due(local(2026, 10, 27, 11, 30))


async def test_briefing_lists_today_and_unread_mail_once_a_day(db_session: AsyncSession) -> None:
    calendar = FakeCalendar(
        [
            make_event("e1", local(2026, 10, 27, 9), summary="Họp giao ban"),
            make_event("e2", local(2026, 10, 27, 14), summary="Đã hủy", status="cancelled"),
        ]
    )
    mail = FakeMail(["Báo giá mới"])
    job = _job(FakeSources(calendar=calendar, mail=mail))
    now = local(2026, 10, 27, 7, 45)

    created = await job.run(db_session, "user_a", now)
    again = await job.run(db_session, "user_a", local(2026, 10, 27, 8, 0))

    assert len(created) == 1 and again == []
    notification = created[0]
    assert notification.kind == "morning_briefing"
    assert notification.dedup_key == "briefing:2026-10-27"
    assert notification.title == "Bản tin sáng 27/10: 1 lịch, 1 email chưa đọc"
    assert "09:00–10:00 Họp giao ban" in notification.body
    assert "Đã hủy" not in notification.body
    assert mail.queries == ["in:inbox is:unread newer_than:1d"]
    window = calendar.calls[0]
    assert window["time_min"] == local(2026, 10, 27, 0)
    assert window["time_max"] == local(2026, 10, 28, 0)


async def test_briefing_is_not_sent_outside_the_window(db_session: AsyncSession) -> None:
    sources = FakeSources()
    assert await _job(sources).run(db_session, "user_a", local(2026, 10, 27, 6)) == []
    disabled = ProactiveSettings(briefing_enabled=False)
    assert await _job(sources, disabled).run(db_session, "user_a", local(2026, 10, 27, 8)) == []
    assert sources.calendar_service.calls == []


async def test_failing_source_is_reported_inside_the_briefing(db_session: AsyncSession) -> None:
    sources = FakeSources(
        calendar=FakeCalendar(error=NotFoundError("no grant")),
        mail=FakeMail(error=NotFoundError("no grant")),
    )
    created = await _job(sources).run(db_session, "user_a", local(2026, 10, 27, 8))

    assert len(created) == 1
    body = created[0].body
    assert "Không đọc được lịch Google." in body
    assert "Không đọc được hộp thư Gmail." in body
    assert await NotificationService.exists(db_session, "user_a", "briefing:2026-10-27")


async def test_empty_day_says_so(db_session: AsyncSession) -> None:
    created = await _job(FakeSources()).run(db_session, "user_a", local(2026, 10, 27, 8))
    assert "Không có lịch nào." in created[0].body
    assert "Không có email mới." in created[0].body


async def test_briefing_lists_overdue_and_due_today_tasks(db_session: AsyncSession) -> None:
    class Tasks:
        async def list_tasks(self) -> list[TaskItem]:
            return [
                TaskItem(id="1", title="Nộp báo cáo", due=date(2026, 10, 27)),
                TaskItem(id="2", title="Gọi [Nam](x)", due=date(2026, 10, 20)),
                TaskItem(id="3", title="Đọc sách", due=date(2026, 11, 2)),
                TaskItem(id="4", title="Không hạn"),
            ]

    created = await _job(FakeSources(tasks=Tasks())).run(
        db_session, "user_a", local(2026, 10, 27, 8)
    )
    body = created[0].body
    assert "**Việc đến hạn** (2)" in body
    assert body.index("Gọi [Nam] (x) — quá hạn") < body.index("- Nộp báo cáo")
    assert "Đọc sách" not in body and "Không hạn" not in body


async def test_briefing_omits_tasks_without_the_grant(db_session: AsyncSession) -> None:
    created = await _job(FakeSources()).run(db_session, "user_a", local(2026, 10, 27, 8))
    assert "Việc đến hạn" not in created[0].body
