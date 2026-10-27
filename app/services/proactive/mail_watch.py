"""Mail watch job: announce important new mail shortly after it arrives."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.infrastructure.db.models import Notification
from app.services.proactive.formatting import plain, sender_name
from app.services.proactive.notifications import NotificationService
from app.services.proactive.sources import ProactiveSources
from app.services.routing.query.parsing import load_gmail_details

MAIL_KIND = "mail_alert"
MAX_PER_RUN = 5


class MailWatchJob:
    """Poll the inbox every few minutes and notify once per matching, recent message.

    Gmail search (``mail_watch_query``, by default unread mail Gmail marks important)
    decides what is worth a notification; only mail received within
    ``mail_watch_max_age_minutes`` is announced, so connecting an old inbox does not
    flood the bell. Subjects and senders are untrusted: they are escaped in the body, and
    the suggested request names the sender's address only.
    """

    name = "mail_watch"

    def __init__(self, sources: ProactiveSources, settings: ProactiveSettings) -> None:
        self._sources = sources
        self._settings = settings
        self._last_run: dict[str, datetime] = {}

    async def run(self, session: AsyncSession, user_id: str, now: datetime) -> list[Notification]:
        """Notify about each new matching message, at most every check interval."""
        if not self._settings.mail_watch_enabled:
            return []
        interval = timedelta(minutes=self._settings.mail_watch_interval_minutes)
        last = self._last_run.get(user_id)
        if last is not None and now - last < interval:
            return []
        self._last_run[user_id] = now

        service = await self._sources.communication(session, user_id)
        page = await service.search_messages(self._settings.mail_watch_query, page_size=10)
        messages = await load_gmail_details(service, list(getattr(page, "items", []) or []))
        cutoff = now - timedelta(minutes=self._settings.mail_watch_max_age_minutes)
        created: list[Notification] = []
        for message in messages:
            if len(created) >= MAX_PER_RUN:
                break
            if not _received_after(message, cutoff):
                continue
            dedup_key = f"mail:{message.get('id')}"
            if await NotificationService.exists(session, user_id, dedup_key):
                continue
            notification = await self._notify(session, user_id, message, dedup_key)
            if notification is not None:
                created.append(notification)
        return created

    async def _notify(
        self, session: AsyncSession, user_id: str, message: dict[str, Any], dedup_key: str
    ) -> Notification | None:
        sender = sender_name(message.get("from") or "Không rõ người gửi")
        subject = plain(message.get("subject") or "(không tiêu đề)")
        snippet = plain(message.get("snippet") or "", 300)
        from_email = str(message.get("from_email") or "")
        data: dict[str, Any] = {
            "message_id": message.get("id"),
            "thread_id": message.get("thread_id"),
        }
        if from_email:
            data["action_label"] = "Đọc email"
            data["suggested_query"] = f"Đọc email mới nhất từ {from_email}"
        return await NotificationService.create(
            session,
            user_id=user_id,
            kind=MAIL_KIND,
            title=f"Email quan trọng: {subject}",
            body=f"**Từ:** {sender}\n\n{snippet}",
            dedup_key=dedup_key,
            data=data,
        )


def _received_after(message: dict[str, Any], cutoff: datetime) -> bool:
    when = message.get("when") or ""
    try:
        received = datetime.fromisoformat(str(when))
    except ValueError:
        return False
    return received >= cutoff
