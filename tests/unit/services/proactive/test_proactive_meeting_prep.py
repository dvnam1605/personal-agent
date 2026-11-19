"""Unit tests for the automatic meeting preparation job."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.domain.errors import NotFoundError
from app.domain.models.google.calendar import CalendarEvent, CalendarEventTime
from app.services.proactive import MeetingPrepJob
from tests.unit.services.proactive._fakes import (
    ZONE,
    FakeCalendar,
    FakeMail,
    FakeSources,
    local,
    make_event,
)

SETTINGS = ProactiveSettings(meeting_prep_lead_minutes=30)
NOW = local(2026, 10, 27, 9, 40)


async def test_prepares_a_meeting_about_to_start_once(db_session: AsyncSession) -> None:
    meeting = make_event(
        "m1", local(2026, 10, 27, 10), summary="Họp TechConsult", attendees=["nam@x.vn"]
    )
    sources = FakeSources(
        calendar=FakeCalendar([meeting]),
        mail=FakeMail(["Gửi anh bản kế hoạch"]),
        retrieve_result=SimpleNamespace(answer="Đồ án dùng FastAPI.", citations=[]),
    )
    job = MeetingPrepJob(sources, SETTINGS)

    created = await job.run(db_session, "user_a", NOW)
    again = await job.run(db_session, "user_a", NOW + timedelta(minutes=1))

    assert len(created) == 1 and again == []
    notification = created[0]
    assert notification.kind == "meeting_prep"
    assert notification.title == "Sắp họp lúc 10:00: Họp TechConsult"
    assert "nam@x.vn: Gửi anh bản kế hoạch" in notification.body
    assert "Đồ án dùng FastAPI." in notification.body
    assert notification.data["meeting"]["id"] == "m1"
    assert sources.retrieved == ["Họp TechConsult"]
    assert sources.calendar_service.calls[0]["time_max"] == NOW + timedelta(minutes=30)


async def test_moved_meeting_is_prepared_again(db_session: AsyncSession) -> None:
    calendar = FakeCalendar([make_event("m1", local(2026, 10, 27, 10), attendees=["a@x.vn"])])
    job = MeetingPrepJob(FakeSources(calendar=calendar), SETTINGS)
    assert len(await job.run(db_session, "user_a", NOW)) == 1

    calendar.events = [make_event("m1", local(2026, 10, 27, 10, 5), attendees=["a@x.vn"])]
    assert len(await job.run(db_session, "user_a", NOW)) == 1


async def test_skips_events_that_are_not_meetings(db_session: AsyncSession) -> None:
    all_day = CalendarEvent(
        id="all-day",
        summary="Nghỉ phép",
        start=CalendarEventTime(value=date(2026, 10, 27), all_day=True, time_zone=ZONE),
        end=CalendarEventTime(value=date(2026, 10, 28), all_day=True, time_zone=ZONE),
    )
    events = [
        make_event("solo", local(2026, 10, 27, 10), summary="Đi ăn trưa"),
        make_event("cancelled", local(2026, 10, 27, 10), attendees=["a@x.vn"], status="cancelled"),
        make_event("later", local(2026, 10, 27, 11), attendees=["a@x.vn"]),
        make_event("started", local(2026, 10, 27, 9, 30), attendees=["a@x.vn"]),
        all_day,
    ]
    sources = FakeSources(calendar=FakeCalendar(events))

    assert await MeetingPrepJob(sources, SETTINGS).run(db_session, "user_a", NOW) == []
    assert sources.retrieved == []


async def test_knowledge_failure_still_delivers_the_dossier(db_session: AsyncSession) -> None:
    meeting = make_event("m1", local(2026, 10, 27, 10), attendees=["a@x.vn"])
    sources = FakeSources(
        calendar=FakeCalendar([meeting]), retrieve_error=NotFoundError("no corpus")
    )
    created = await MeetingPrepJob(sources, SETTINGS).run(db_session, "user_a", NOW)

    assert len(created) == 1
    assert "Tài liệu nội bộ" not in created[0].body


async def test_disabled_job_reads_nothing(db_session: AsyncSession) -> None:
    sources = FakeSources(calendar=FakeCalendar([make_event("m1", local(2026, 10, 27, 10))]))
    disabled = ProactiveSettings(meeting_prep_enabled=False)
    assert await MeetingPrepJob(sources, disabled).run(db_session, "user_a", NOW) == []
    assert sources.calendar_service.calls == []
