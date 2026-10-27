"""Unit tests for the meeting follow-up suggestion, the mail watch and text escaping."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.services.proactive import MailWatchJob, MeetingFollowupJob
from app.services.proactive.formatting import plain, sender_name
from tests.unit.services.proactive._fakes import (
    FakeCalendar,
    FakeMail,
    FakeSources,
    local,
    make_event,
    make_mail,
)

SETTINGS = ProactiveSettings(
    followup_lookback_minutes=120, mail_watch_interval_minutes=5, mail_watch_max_age_minutes=60
)


def test_plain_breaks_markup_without_escapes() -> None:
    assert plain("[click](http://evil)\n**now** $x$") == "[click] (http://evil) **now** ＄x＄"
    assert sender_name("LinkedIn <jobs@linkedin.com>") == "LinkedIn"
    assert sender_name("nam@x.vn") == "nam@x.vn"
    assert plain("x" * 50, max_len=10) == "xxxxxxxxx…"
    assert plain(None) == ""


async def test_followup_is_suggested_once_after_a_meeting_ends(db_session: AsyncSession) -> None:
    ended = make_event(
        "m1", local(2026, 10, 27, 9), summary="Họp [link](x)", attendees=["nam@x.vn"]
    )
    running = make_event("m2", local(2026, 10, 27, 10, 30), attendees=["nam@x.vn"])
    solo = make_event("m3", local(2026, 10, 27, 9), summary="Tự làm")
    calendar = FakeCalendar([ended, running, solo])
    job = MeetingFollowupJob(FakeSources(calendar=calendar), SETTINGS)
    now = local(2026, 10, 27, 10, 45)

    created = await job.run(db_session, "user_a", now)
    again = await job.run(db_session, "user_a", now + timedelta(minutes=1))

    assert len(created) == 1 and again == []
    notification = created[0]
    assert notification.kind == "meeting_followup"
    assert notification.title == "Đã họp xong: Họp [link] (x)"
    assert notification.data["suggested_query"] == "Soạn email follow-up cuộc họp hôm nay"
    assert "link" not in notification.data["suggested_query"]
    assert "nam@x.vn" in notification.body
    assert calendar.calls[0]["time_min"] == now - timedelta(minutes=120)


async def test_followup_for_yesterdays_meeting_says_yesterday(db_session: AsyncSession) -> None:
    late = make_event("m1", local(2026, 10, 26, 23), attendees=["nam@x.vn"], minutes=30)
    job = MeetingFollowupJob(FakeSources(calendar=FakeCalendar([late])), SETTINGS)
    created = await job.run(db_session, "user_a", local(2026, 10, 27, 0, 15))
    assert created[0].data["suggested_query"] == "Soạn email follow-up cuộc họp hôm qua"


async def test_followup_disabled(db_session: AsyncSession) -> None:
    sources = FakeSources()
    job = MeetingFollowupJob(sources, ProactiveSettings(followup_enabled=False))
    assert await job.run(db_session, "user_a", local(2026, 10, 27, 10)) == []
    assert sources.calendar_service.calls == []


async def test_mail_watch_announces_recent_mail_once(db_session: AsyncSession) -> None:
    now = local(2026, 10, 27, 10)
    mail = FakeMail(
        messages=[
            make_mail("new", now - timedelta(minutes=10), subject="Hợp đồng ![x](http://e)"),
            make_mail("old", now - timedelta(hours=3)),
        ]
    )
    job = MailWatchJob(FakeSources(mail=mail), SETTINGS)

    created = await job.run(db_session, "user_a", now)
    throttled = await job.run(db_session, "user_a", now + timedelta(minutes=2))
    later = await job.run(db_session, "user_a", now + timedelta(minutes=6))

    assert [n.dedup_key for n in created] == ["mail:new"]
    assert throttled == [] and later == []
    assert len(mail.queries) == 2
    notification = created[0]
    assert notification.title == "Email quan trọng: Hợp đồng ![x] (http://e)"
    assert notification.data["suggested_query"] == "Đọc email mới nhất từ nam@x.vn"
    assert "Hợp đồng" not in notification.data["suggested_query"]


async def test_mail_watch_caps_notifications_per_run(db_session: AsyncSession) -> None:
    now = local(2026, 10, 27, 10)
    mail = FakeMail(messages=[make_mail(f"m{i}", now - timedelta(minutes=i)) for i in range(8)])
    created = await MailWatchJob(FakeSources(mail=mail), SETTINGS).run(db_session, "user_a", now)
    assert len(created) == 5


async def test_mail_watch_disabled(db_session: AsyncSession) -> None:
    sources = FakeSources()
    job = MailWatchJob(sources, ProactiveSettings(mail_watch_enabled=False))
    assert await job.run(db_session, "user_a", local(2026, 10, 27, 10)) == []
    assert sources.mail_service.queries == []
