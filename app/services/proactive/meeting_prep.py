"""Meeting preparation job: build the dossier of each meeting shortly before it starts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.domain.errors import AppError
from app.infrastructure.db.models import Notification
from app.services.proactive.notifications import NotificationService
from app.services.proactive.sources import ProactiveSources
from app.services.routing.live_workflows import build_meeting_dossier

MEETING_PREP_KIND = "meeting_prep"
MAX_EVENTS = 20


class MeetingPrepJob:
    """Prepare every meeting that starts within the lead time, once per start time.

    Only timed, confirmed events with at least one other attendee count as meetings, so
    a personal reminder does not trigger a document search. The dedup key includes the
    start time: a meeting moved to another hour is prepared again for the new slot. The
    dossier comes from the same read-only builder the "chuẩn bị họp" workflow uses.
    """

    name = "meeting_prep"

    def __init__(self, sources: ProactiveSources, settings: ProactiveSettings) -> None:
        self._sources = sources
        self._settings = settings
        self._zone = ZoneInfo(settings.timezone)

    async def run(self, session: AsyncSession, user_id: str, now: datetime) -> list[Notification]:
        """Create a dossier notification for each meeting about to start."""
        if not self._settings.meeting_prep_enabled:
            return []
        horizon = now + timedelta(minutes=self._settings.meeting_prep_lead_minutes)
        calendar = await self._sources.calendar(session, user_id)
        page = await calendar.list_events(
            "primary",
            time_min=now,
            time_max=horizon,
            page_size=MAX_EVENTS,
            time_zone=self._settings.timezone,
        )
        created: list[Notification] = []
        for event in list(getattr(page, "items", []) or []):
            start = _meeting_start(event)
            if start is None or not now < start <= horizon:
                continue
            dedup_key = f"meeting_prep:{event.id}:{start.isoformat()}"
            if await NotificationService.exists(session, user_id, dedup_key):
                continue
            notification = await self._prepare(session, user_id, event, start, dedup_key)
            if notification is not None:
                created.append(notification)
        return created

    async def _prepare(
        self,
        session: AsyncSession,
        user_id: str,
        event: Any,
        start: datetime,
        dedup_key: str,
    ) -> Notification | None:
        communication = await self._sources.communication(session, user_id)

        async def search_messages(mail_query: str, page_size: int = 3) -> Any:
            return await communication.search_messages(mail_query, page_size=page_size)

        async def retrieve(topic: str) -> Any:
            try:
                return await self._sources.retrieve(topic, user_id)
            except AppError:
                return None

        message, data = await build_meeting_dossier(
            event, search_messages=search_messages, retrieve=retrieve
        )
        title = getattr(event, "summary", "") or "(không tiêu đề)"
        local = start.astimezone(self._zone)
        return await NotificationService.create(
            session,
            user_id=user_id,
            kind=MEETING_PREP_KIND,
            title=f"Sắp họp lúc {local:%H:%M}: {title}",
            body=message,
            dedup_key=dedup_key,
            data={"meeting": data.get("meeting", {}), "documents": data.get("documents", [])},
        )


def _meeting_start(event: Any) -> datetime | None:
    """Start of a timed, confirmed event with another attendee; ``None`` otherwise."""
    if getattr(event, "status", "confirmed") == "cancelled":
        return None
    start = getattr(event, "start", None)
    if start is None or getattr(start, "all_day", False):
        return None
    if not other_attendee_emails(event):
        return None
    try:
        return start.as_datetime()
    except (TypeError, ValueError):
        return None


def other_attendee_emails(event: Any) -> list[str]:
    """Emails of the attendees other than the calendar owner."""
    return [
        str(attendee.email)
        for attendee in list(getattr(event, "attendees", []) or [])
        if not getattr(attendee, "self_attendee", False) and getattr(attendee, "email", "")
    ]
