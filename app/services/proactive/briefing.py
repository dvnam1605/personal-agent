"""Morning briefing job: today's calendar and the unread mail of the last day, once a day."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.domain.errors import AppError
from app.infrastructure.db.models import Notification
from app.services.proactive.formatting import plain
from app.services.proactive.notifications import NotificationService
from app.services.proactive.sources import ProactiveSources
from app.services.routing.query.parsing import load_gmail_details

BRIEFING_KIND = "morning_briefing"
UNREAD_MAIL_QUERY = "in:inbox is:unread newer_than:1d"
MAX_EVENTS = 20
MAX_MAILS = 10


class MorningBriefingJob:
    """Leave one briefing per local day once the configured time has passed.

    The briefing is built from plain reads and written without an LLM call, so it is
    cheap, deterministic and never invents an appointment. A section whose source fails
    says so instead of dropping the whole briefing. Titles, senders and subjects come
    from other people and are escaped before they reach the Markdown body.
    """

    name = "morning_briefing"

    def __init__(self, sources: ProactiveSources, settings: ProactiveSettings) -> None:
        self._sources = sources
        self._settings = settings
        self._zone = ZoneInfo(settings.timezone)

    def is_due(self, now: datetime) -> bool:
        """Whether ``now`` falls in today's briefing window (briefing time + catch-up)."""
        local = now.astimezone(self._zone)
        start = datetime.combine(local.date(), self._settings.briefing_time, tzinfo=self._zone)
        end = start + timedelta(hours=self._settings.briefing_catch_up_hours)
        return start <= local < end

    async def run(self, session: AsyncSession, user_id: str, now: datetime) -> list[Notification]:
        """Create today's briefing for the user when it is due and not yet sent."""
        if not self._settings.briefing_enabled or not self.is_due(now):
            return []
        local = now.astimezone(self._zone)
        dedup_key = f"briefing:{local.date().isoformat()}"
        if await NotificationService.exists(session, user_id, dedup_key):
            return []

        events_section, event_count = await self._events_section(session, user_id, local)
        mail_section, mail_count = await self._mail_section(session, user_id)
        body = "\n\n".join([events_section, mail_section])
        notification = await NotificationService.create(
            session,
            user_id=user_id,
            kind=BRIEFING_KIND,
            title=f"Bản tin sáng {local:%d/%m}: {event_count} lịch, {mail_count} email chưa đọc",
            body=body,
            dedup_key=dedup_key,
            data={"date": local.date().isoformat(), "events": event_count, "mails": mail_count},
        )
        return [notification] if notification is not None else []

    async def _events_section(
        self, session: AsyncSession, user_id: str, local: datetime
    ) -> tuple[str, int]:
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            calendar = await self._sources.calendar(session, user_id)
            page = await calendar.list_events(
                "primary",
                time_min=day_start,
                time_max=day_start + timedelta(days=1),
                page_size=MAX_EVENTS,
                time_zone=self._settings.timezone,
            )
        except AppError:
            return "**Lịch hôm nay**\nKhông đọc được lịch Google.", 0
        events = [
            event
            for event in list(getattr(page, "items", []) or [])
            if getattr(event, "status", "confirmed") != "cancelled"
        ]
        if not events:
            return "**Lịch hôm nay**\nKhông có lịch nào.", 0
        lines = [f"**Lịch hôm nay** ({len(events)})"]
        lines.extend(f"- {self._event_line(event)}" for event in events)
        return "\n".join(lines), len(events)

    async def _mail_section(self, session: AsyncSession, user_id: str) -> tuple[str, int]:
        try:
            service = await self._sources.communication(session, user_id)
            page = await service.search_messages(UNREAD_MAIL_QUERY, page_size=MAX_MAILS)
            messages = await load_gmail_details(service, list(getattr(page, "items", []) or []))
        except AppError:
            return "**Email chưa đọc (24 giờ qua)**\nKhông đọc được hộp thư Gmail.", 0
        if not messages:
            return "**Email chưa đọc (24 giờ qua)**\nKhông có email mới.", 0
        lines = [f"**Email chưa đọc (24 giờ qua)** ({len(messages)})"]
        lines.extend(
            f"- {plain(message.get('from') or 'Không rõ người gửi', 120)}: "
            f"{plain(message.get('subject'))}"
            for message in messages
        )
        return "\n".join(lines), len(messages)

    def _event_line(self, event: Any) -> str:
        title = plain(getattr(event, "summary", "") or "(không tiêu đề)")
        start = getattr(event, "start", None)
        end = getattr(event, "end", None)
        if start is None or getattr(start, "all_day", False):
            return f"Cả ngày — {title}"
        try:
            begin = start.as_datetime().astimezone(self._zone)
            finish = end.as_datetime().astimezone(self._zone) if end is not None else None
        except (TypeError, ValueError):
            return title
        span = f"{begin:%H:%M}" + (f"–{finish:%H:%M}" if finish is not None else "")
        location = plain(getattr(event, "location", None) or "", 120)
        return f"{span} {title}" + (f" ({location})" if location else "")
