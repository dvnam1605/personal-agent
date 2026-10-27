"""Unit tests for the proactive worker loop: who it runs for and how failures are isolated."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.settings.proactive import ProactiveSettings
from app.infrastructure.db.models import GoogleIntegration, Notification, User
from app.services.proactive import NotificationService, ProactiveWorker
from tests.unit.services.proactive._fakes import FakeSources

NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


class RecordingJob:
    """Creates one notification per call; fails for the users listed in ``fail_for``."""

    def __init__(self, name: str = "recording", fail_for: set[str] | None = None) -> None:
        self.name = name
        self.fail_for = fail_for or set()
        self.calls: list[str] = []

    async def run(self, session: AsyncSession, user_id: str, now: datetime) -> list[Notification]:
        self.calls.append(user_id)
        if user_id in self.fail_for:
            raise RuntimeError("google down")
        created = await NotificationService.create(
            session,
            user_id=user_id,
            kind="test",
            title="t",
            body="b",
            dedup_key=f"{self.name}:{len(self.calls)}",
        )
        return [created] if created is not None else []


async def _connect(factory: async_sessionmaker[AsyncSession], *user_ids: str) -> None:
    async with factory() as session:
        session.add_all(
            GoogleIntegration(user_id=user_id, refresh_token_encrypted="enc")
            for user_id in user_ids
        )
        await session.commit()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def test_runs_only_for_active_connected_users(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _connect(session_factory, "user_a", "user_b")
    async with session_factory() as session:
        session.add(User(id="user_c", email="c@example.com"))
        await session.commit()
        await session.execute(
            update(GoogleIntegration)
            .where(GoogleIntegration.user_id == "user_b")
            .values(revoked_at=NOW)
        )
        await session.commit()
    job = RecordingJob()
    worker = ProactiveWorker(session_factory, [job], ProactiveSettings(), clock=lambda: NOW)

    assert await worker.run_once() == 1
    assert job.calls == ["user_a"]


async def test_failing_user_is_paused_and_does_not_block_others(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _connect(session_factory, "user_a", "user_b")
    job = RecordingJob(fail_for={"user_a"})
    clock = Clock(NOW)
    settings = ProactiveSettings(failure_backoff_seconds=600)
    worker = ProactiveWorker(session_factory, [job], settings, clock=clock)

    assert await worker.run_once() == 1
    clock.now = NOW + timedelta(minutes=5)
    await worker.run_once()
    assert job.calls.count("user_a") == 1

    clock.now = NOW + timedelta(minutes=11)
    job.fail_for.clear()
    await worker.run_once()
    assert job.calls.count("user_a") == 2
    async with session_factory() as session:
        owners = list(await session.scalars(select(Notification.user_id)))
    assert owners.count("user_a") == 1
    assert owners.count("user_b") == 3


async def test_purges_old_notifications_once_a_day(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await NotificationService.create(
            session, user_id="user_a", kind="t", title="t", body="b", dedup_key="old"
        )
        await session.commit()
        await session.execute(update(Notification).values(created_at=NOW - timedelta(days=31)))
        await session.commit()
    worker = ProactiveWorker(session_factory, [], ProactiveSettings(), clock=lambda: NOW)

    await worker.run_once()
    async with session_factory() as session:
        assert list(await session.scalars(select(Notification.id))) == []


async def test_default_jobs_and_stop_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    worker = ProactiveWorker.with_default_jobs(
        session_factory, FakeSources(), ProactiveSettings(tick_seconds=0.01)
    )
    assert [job.name for job in worker.jobs] == [
        "morning_briefing",
        "meeting_prep",
        "meeting_followup",
        "meeting_minutes",
        "mail_watch",
        "watcher",
        "goal_reminder",
    ]

    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
