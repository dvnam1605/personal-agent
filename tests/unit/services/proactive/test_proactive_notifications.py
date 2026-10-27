"""Unit tests for owner-bound, once-per-occurrence notification storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Notification
from app.services.proactive import NotificationService


async def _create(session: AsyncSession, user_id: str, key: str) -> Notification | None:
    return await NotificationService.create(
        session, user_id=user_id, kind="test", title=f"T {key}", body="body", dedup_key=key
    )


async def test_create_is_once_per_occurrence_and_per_user(db_session: AsyncSession) -> None:
    first = await _create(db_session, "user_a", "briefing:2026-10-27")
    duplicate = await _create(db_session, "user_a", "briefing:2026-10-27")
    other_user = await _create(db_session, "user_b", "briefing:2026-10-27")
    await db_session.commit()

    assert first is not None
    assert duplicate is None
    assert other_user is not None
    assert await NotificationService.exists(db_session, "user_a", "briefing:2026-10-27")
    assert not await NotificationService.exists(db_session, "user_a", "briefing:2026-10-28")


async def test_create_truncates_title_and_body(db_session: AsyncSession) -> None:
    notification = await NotificationService.create(
        db_session,
        user_id="user_a",
        kind="test",
        title="x" * 400,
        body="y" * 9000,
        dedup_key="long",
    )
    assert notification is not None
    assert len(notification.title) == 255
    assert len(notification.body) == 8000


async def test_list_and_mark_read_are_scoped_to_the_owner(db_session: AsyncSession) -> None:
    mine = await _create(db_session, "user_a", "k1")
    await _create(db_session, "user_a", "k2")
    theirs = await _create(db_session, "user_b", "k3")
    assert mine is not None and theirs is not None
    await db_session.flush()

    assert {n.dedup_key for n in await NotificationService.list_for_user(db_session, "user_a")} == {
        "k1",
        "k2",
    }
    assert await NotificationService.unread_count(db_session, "user_a") == 2

    assert not await NotificationService.mark_read(db_session, "user_a", theirs.id)
    assert not await NotificationService.mark_read(db_session, "user_a", "missing")
    assert await NotificationService.mark_read(db_session, "user_a", mine.id)
    assert await NotificationService.mark_read(db_session, "user_a", mine.id)
    assert await NotificationService.unread_count(db_session, "user_a") == 1
    unread = await NotificationService.list_for_user(db_session, "user_a", unread_only=True)
    assert [n.dedup_key for n in unread] == ["k2"]
    assert await NotificationService.unread_count(db_session, "user_b") == 1


async def test_mark_all_read_only_touches_the_caller(db_session: AsyncSession) -> None:
    await _create(db_session, "user_a", "k1")
    await _create(db_session, "user_a", "k2")
    await _create(db_session, "user_b", "k3")
    await db_session.flush()

    assert await NotificationService.mark_all_read(db_session, "user_a") == 2
    assert await NotificationService.unread_count(db_session, "user_a") == 0
    assert await NotificationService.unread_count(db_session, "user_b") == 1


async def test_purge_deletes_only_old_notifications(db_session: AsyncSession) -> None:
    old = await _create(db_session, "user_a", "old")
    await _create(db_session, "user_a", "new")
    assert old is not None
    await db_session.flush()
    now = datetime.now(UTC)
    await db_session.execute(
        update(Notification)
        .where(Notification.id == old.id)
        .values(created_at=now - timedelta(days=40))
    )

    assert await NotificationService.purge_older_than(db_session, now - timedelta(days=30)) == 1
    remaining = await NotificationService.list_for_user(db_session, "user_a")
    assert [n.dedup_key for n in remaining] == ["new"]
