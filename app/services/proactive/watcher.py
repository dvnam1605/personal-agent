"""Watcher job: report what the user asked to watch — a sender, a phrase, new documents."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.domain.errors import AppError
from app.infrastructure.db.models import Document, Notification, WatchRule
from app.services.goals.service import WatchService
from app.services.proactive.formatting import plain
from app.services.proactive.notifications import NotificationService
from app.services.proactive.sources import ProactiveSources
from app.services.routing.query.parsing import load_gmail_details
from app.services.skills.matching import unaccent_vietnamese

WATCH_KIND = "watch_alert"
MAX_PER_RULE = 5
FIRST_LOOKBACK = timedelta(hours=1)


class WatcherJob:
    """Check each active rule of the user at the mail-watch interval and report matches.

    A rule only reports; it never drafts, sends or edits anything. Mail rules search
    Gmail and announce messages received since the rule was last checked; document
    rules look for newly ingested documents the user may read whose title mentions the
    topic. Each match is announced once per rule. Expired rules are switched off.
    """

    name = "watcher"

    def __init__(self, sources: ProactiveSources, settings: ProactiveSettings) -> None:
        self._sources = sources
        self._settings = settings

    async def run(self, session: AsyncSession, user_id: str, now: datetime) -> list[Notification]:
        created: list[Notification] = []
        interval = timedelta(minutes=self._settings.mail_watch_interval_minutes)
        for rule in await WatchService.active(session, user_id):
            if rule.expires_at is not None and _aware(rule.expires_at) <= now:
                rule.active = False
                continue
            since = _aware(rule.last_checked_at) if rule.last_checked_at else now - FIRST_LOOKBACK
            if now - since < interval:
                continue
            if rule.kind == "document_topic":
                created += await self._documents(session, user_id, rule, since)
            else:
                created += await self._mail(session, user_id, rule, since)
            rule.last_checked_at = now
        await session.flush()
        return created

    async def _mail(
        self, session: AsyncSession, user_id: str, rule: WatchRule, since: datetime
    ) -> list[Notification]:
        if rule.kind == "mail_from":
            gmail_query = f"from:{rule.target} newer_than:2d"
        else:
            phrase = rule.target.replace('"', " ").strip()
            gmail_query = f'"{phrase}" newer_than:2d'
        try:
            service = await self._sources.communication(session, user_id)
            page = await service.search_messages(gmail_query, page_size=MAX_PER_RULE)
            messages = await load_gmail_details(service, list(getattr(page, "items", []) or []))
        except AppError:
            return []
        created: list[Notification] = []
        for message in messages:
            if not _after(message.get("when"), since):
                continue
            label = (
                f"Email mới từ {plain(rule.target)}"
                if rule.kind == "mail_from"
                else f"Email nhắc đến «{plain(rule.target)}»"
            )
            data: dict[str, Any] = {"rule_id": rule.id, "message_id": message.get("id")}
            if message.get("from_email"):
                data["action_label"] = "Đọc email"
                data["suggested_query"] = f"Đọc email mới nhất từ {message['from_email']}"
            notification = await NotificationService.create(
                session,
                user_id=user_id,
                kind=WATCH_KIND,
                title=f"{label}: {plain(message.get('subject') or '(không tiêu đề)')}",
                body=f"**Từ:** {plain(message.get('from') or '')}\n\n{plain(message.get('snippet') or '', 300)}",
                dedup_key=f"watch:{rule.id}:{message.get('id')}",
                data=data,
            )
            if notification is not None:
                created.append(notification)
        return created

    async def _documents(
        self, session: AsyncSession, user_id: str, rule: WatchRule, since: datetime
    ) -> list[Notification]:
        words = [word for word in unaccent_vietnamese(rule.target).split() if len(word) > 1]
        rows = await session.scalars(
            select(Document)
            .where(
                Document.is_active.is_(True),
                Document.created_at > since,
                or_(Document.user_id.is_(None), Document.user_id == user_id),
            )
            .order_by(Document.created_at.desc())
            .limit(50)
        )
        created: list[Notification] = []
        for document in rows.all():
            title = unaccent_vietnamese(document.title or "")
            if not all(word in title for word in words):
                continue
            notification = await NotificationService.create(
                session,
                user_id=user_id,
                kind=WATCH_KIND,
                title=f"Tài liệu mới về «{plain(rule.target)}»: {plain(document.title)}",
                body=f"Kho tài liệu vừa có văn bản **{plain(document.title)}**.",
                dedup_key=f"watch:{rule.id}:{document.logical_document_id}",
                data={
                    "rule_id": rule.id,
                    "document_id": document.id,
                    "action_label": "Tóm tắt",
                    "suggested_query": f"Tóm tắt tài liệu về {rule.target}",
                },
            )
            if notification is not None:
                created.append(notification)
        return created


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; they were stored in UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _after(when: Any, since: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(when)) > since
    except ValueError:
        return False
