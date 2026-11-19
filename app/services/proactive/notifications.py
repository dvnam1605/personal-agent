"""Owner-bound storage of notifications: write once per occurrence, read and acknowledge."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Notification

TITLE_MAX_LEN = 255
BODY_MAX_LEN = 8000


class NotificationService:
    """Create, list and mark notifications; every read and update is scoped to its owner.

    ``create`` relies on the ``(user_id, dedup_key)`` unique constraint: a second write
    for the same occurrence is dropped inside a savepoint and returns ``None``, so a job
    may simply try again after a restart without checking first.
    """

    @staticmethod
    async def exists(session: AsyncSession, user_id: str, dedup_key: str) -> bool:
        """Whether this occurrence was already notified, so costly work can be skipped."""
        found = await session.scalar(
            select(Notification.id).where(
                Notification.user_id == user_id, Notification.dedup_key == dedup_key
            )
        )
        return found is not None

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        user_id: str,
        kind: str,
        title: str,
        body: str,
        dedup_key: str,
        data: dict[str, Any] | None = None,
    ) -> Notification | None:
        """Store a notification unless this occurrence is already stored."""
        notification = Notification(
            user_id=user_id,
            kind=kind,
            title=title[:TITLE_MAX_LEN],
            body=body[:BODY_MAX_LEN],
            data=data or {},
            dedup_key=dedup_key,
        )
        try:
            async with session.begin_nested():
                session.add(notification)
        except IntegrityError:
            return None
        return notification

    @staticmethod
    async def list_for_user(
        session: AsyncSession, user_id: str, *, unread_only: bool = False, limit: int = 20
    ) -> list[Notification]:
        """Newest notifications of one user."""
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
        return list((await session.scalars(stmt)).all())

    @staticmethod
    async def unread_count(session: AsyncSession, user_id: str) -> int:
        """Number of unread notifications of one user."""
        count = await session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        return int(count or 0)

    @staticmethod
    async def mark_read(session: AsyncSession, user_id: str, notification_id: str) -> bool:
        """Mark one of the user's notifications read; False when it is not theirs or missing."""
        notification = await session.get(Notification, notification_id)
        if notification is None or notification.user_id != user_id:
            return False
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            await session.flush()
        return True

    @staticmethod
    async def mark_all_read(session: AsyncSession, user_id: str) -> int:
        """Mark every unread notification of the user read; returns how many changed."""
        result = await session.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            .values(read_at=datetime.now(UTC))
        )
        return _row_count(result)

    @staticmethod
    async def purge_older_than(session: AsyncSession, cutoff: datetime) -> int:
        """Delete notifications created before ``cutoff``."""
        result = await session.execute(delete(Notification).where(Notification.created_at < cutoff))
        return _row_count(result)


def _row_count(result: object) -> int:
    value = getattr(result, "rowcount", 0)
    return value if isinstance(value, int) else 0
