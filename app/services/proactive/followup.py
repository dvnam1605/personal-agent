"""Meeting follow-up job: after a meeting ends, offer to draft the follow-up mail."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.infrastructure.db.models import Notification
from app.services.proactive.formatting import plain
from app.services.proactive.meeting_prep import other_attendee_emails
from app.services.proactive.notifications import NotificationService
from app.services.proactive.sources import ProactiveSources

FOLLOWUP_KIND = "meeting_followup"
MAX_EVENTS = 20


class MeetingFollowupJob:
    """Suggest a follow-up for each meeting with other attendees that ended recently.

    The job drafts nothing itself: the notification carries a suggested request that the
    user can send to the assistant, which runs the existing follow-up workflow and asks
    for approval before any draft is created. The suggestion names the meeting by its day
    only ("hôm nay"/"hôm qua", which the follow-up workflow resolves to the latest ended
    meeting), never by its invite-controlled title.
    """

    name = "meeting_followup"

    def __init__(self, sources: ProactiveSources, settings: ProactiveSettings) -> None:
        self._sources = sources
        self._settings = settings
        self._zone = ZoneInfo(settings.timezone)

    async def run(self, session: AsyncSession, user_id: str, now: datetime) -> list[Notification]:
        """Create a follow-up suggestion for each meeting that just ended."""
        if not self._settings.followup_enabled:
            return []
        since = now - timedelta(minutes=self._settings.followup_lookback_minutes)
        calendar = await self._sources.calendar(session, user_id)
        page = await calendar.list_events(
            "primary",
            time_min=since,
            time_max=now,
            page_size=MAX_EVENTS,
            time_zone=self._settings.timezone,
        )
        created: list[Notification] = []
        for event in list(getattr(page, "items", []) or []):
            end = _meeting_end(event)
            if end is None or not since < end <= now:
                continue
            dedup_key = f"meeting_followup:{event.id}:{end.isoformat()}"
            if await NotificationService.exists(session, user_id, dedup_key):
                continue
            notification = await self._suggest(session, user_id, event, end, now, dedup_key)
            if notification is not None:
                created.append(notification)
        return created

    async def _suggest(
        self,
        session: AsyncSession,
        user_id: str,
        event: Any,
        end: datetime,
        now: datetime,
        dedup_key: str,
    ) -> Notification | None:
        title = plain(getattr(event, "summary", "") or "(không tiêu đề)")
        attendees = other_attendee_emails(event)
        local_end = end.astimezone(self._zone)
        day = "hôm nay" if local_end.date() == now.astimezone(self._zone).date() else "hôm qua"
        body = "\n".join(
            [
                f"Cuộc họp **{title}** kết thúc lúc {local_end:%H:%M}.",
                f"Khách mời: {', '.join(plain(email) for email in attendees)}",
                "",
                "Bấm «Soạn follow-up» để trợ lý soạn nháp email; bạn duyệt trước khi nháp được tạo.",
            ]
        )
        return await NotificationService.create(
            session,
            user_id=user_id,
            kind=FOLLOWUP_KIND,
            title=f"Đã họp xong: {title}",
            body=body,
            dedup_key=dedup_key,
            data={
                "meeting": {"id": event.id, "attendees": attendees},
                "action_label": "Soạn follow-up",
                "suggested_query": f"Soạn email follow-up cuộc họp {day}",
            },
        )


def _meeting_end(event: Any) -> datetime | None:
    """End of a timed, confirmed event with another attendee; ``None`` otherwise."""
    if getattr(event, "status", "confirmed") == "cancelled":
        return None
    start = getattr(event, "start", None)
    end = getattr(event, "end", None)
    if start is None or end is None or getattr(start, "all_day", False):
        return None
    if not other_attendee_emails(event):
        return None
    try:
        return end.as_datetime()
    except (TypeError, ValueError):
        return None
