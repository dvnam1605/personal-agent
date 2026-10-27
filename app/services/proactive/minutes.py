"""Meeting minutes job: once a meeting's Meet transcript appears in Drive, write minutes."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.domain.errors import AppError
from app.infrastructure.db.models import Notification
from app.services.meetings.minutes import find_transcript, write_minutes
from app.services.proactive.formatting import plain
from app.services.proactive.meeting_prep import other_attendee_emails
from app.services.proactive.notifications import NotificationService
from app.services.proactive.sources import ProactiveSources

MINUTES_KIND = "meeting_minutes"
# Meet writes the transcript some minutes after the call; look for it for this long.
SEARCH_WINDOW = timedelta(hours=6)
RECHECK = timedelta(minutes=10)
MAX_EVENTS = 20


class MeetingMinutesJob:
    """Write minutes for each recently ended meeting whose transcript is in Drive.

    Each meeting is looked up at most every ``RECHECK`` until ``SEARCH_WINDOW`` after it
    ended, then given up on. The transcript reaches the model only inside an untrusted
    boundary, and the minutes are a notification — sharing or mailing them stays a
    request the user makes.
    """

    name = "meeting_minutes"

    def __init__(self, sources: ProactiveSources, settings: ProactiveSettings) -> None:
        self._sources = sources
        self._settings = settings
        self._zone = ZoneInfo(settings.timezone)
        self._last_lookup: dict[str, datetime] = {}

    async def run(self, session: AsyncSession, user_id: str, now: datetime) -> list[Notification]:
        if not self._settings.followup_enabled:
            return []
        calendar = await self._sources.calendar(session, user_id)
        page = await calendar.list_events(
            "primary",
            time_min=now - SEARCH_WINDOW,
            time_max=now,
            page_size=MAX_EVENTS,
            time_zone=self._settings.timezone,
        )
        created: list[Notification] = []
        for event in list(getattr(page, "items", []) or []):
            ended = _ended(event)
            if ended is None or not now - SEARCH_WINDOW < ended <= now:
                continue
            dedup_key = f"minutes:{event.id}:{ended.isoformat()}"
            last = self._last_lookup.get(dedup_key)
            if last is not None and now - last < RECHECK:
                continue
            self._last_lookup[dedup_key] = now
            if await NotificationService.exists(session, user_id, dedup_key):
                continue
            notification = await self._minutes(session, user_id, event, dedup_key)
            if notification is not None:
                created.append(notification)
        return created

    async def _minutes(
        self, session: AsyncSession, user_id: str, event: Any, dedup_key: str
    ) -> Notification | None:
        title = getattr(event, "summary", "") or ""
        started = event.start.as_datetime()
        try:
            drive = await self._sources.drive(session, user_id)
            found = await find_transcript(drive, title, started)
        except AppError:
            return None
        if found is None:
            return None
        transcript_file, text = found
        when = f"{started.astimezone(self._zone):%H:%M %d/%m/%Y}"
        minutes = await write_minutes(
            title, when, other_attendee_emails(event), transcript_file, text
        )
        return await NotificationService.create(
            session,
            user_id=user_id,
            kind=MINUTES_KIND,
            title=f"Biên bản: {plain(title)}",
            body=minutes.markdown(),
            dedup_key=dedup_key,
            data={
                "meeting": {"id": event.id},
                "action_items": minutes.action_items,
                "action_label": "Soạn follow-up",
                "suggested_query": "Soạn email follow-up cuộc họp hôm nay",
            },
        )


def _ended(event: Any) -> datetime | None:
    if getattr(event, "status", "confirmed") == "cancelled" or not other_attendee_emails(event):
        return None
    start, end = getattr(event, "start", None), getattr(event, "end", None)
    if start is None or end is None or getattr(start, "all_day", False):
        return None
    try:
        return end.as_datetime()
    except (TypeError, ValueError):
        return None
