"""Background loop that runs the proactive jobs for every user with Google connected."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.settings.proactive import ProactiveSettings
from app.infrastructure.db.models import GoogleIntegration, Notification, User
from app.services.proactive.briefing import MorningBriefingJob
from app.services.proactive.events import NotificationBus, get_notification_bus
from app.services.proactive.followup import MeetingFollowupJob
from app.services.proactive.goals import GoalReminderJob
from app.services.proactive.mail_watch import MailWatchJob
from app.services.proactive.meeting_prep import MeetingPrepJob
from app.services.proactive.minutes import MeetingMinutesJob
from app.services.proactive.notifications import NotificationService
from app.services.proactive.routines import RoutineJob
from app.services.proactive.sources import ProactiveSources
from app.services.proactive.watcher import WatcherJob

logger = structlog.get_logger(__name__)

PURGE_INTERVAL = timedelta(days=1)


class ProactiveJob(Protocol):
    """A job the worker runs for one user on every tick; it decides itself whether it is due."""

    name: str

    async def run(
        self, session: AsyncSession, user_id: str, now: datetime
    ) -> list[Notification]: ...


class ProactiveWorker:
    """Wake every tick and run each job for each connected user, isolated from the others.

    Each (user, job) pair runs in its own session and commits on its own, so one user's
    expired Google grant or one failing job never blocks the rest. A failure pauses that
    pair for ``failure_backoff_seconds`` instead of retrying a broken call every minute.
    Duplicate runs (restarts, several processes) are harmless: notifications are unique
    per occurrence. Each committed notification is announced on the notification bus so
    open tabs refresh at once. Old notifications are purged once a day.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        jobs: Sequence[ProactiveJob],
        settings: ProactiveSettings,
        clock: Callable[[], datetime] | None = None,
        bus: NotificationBus | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.jobs = list(jobs)
        self.settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._bus = bus
        self._paused_until: dict[tuple[str, str], datetime] = {}
        self._last_purge: datetime | None = None

    @classmethod
    def with_default_jobs(
        cls,
        session_factory: async_sessionmaker[AsyncSession],
        sources: ProactiveSources,
        settings: ProactiveSettings,
    ) -> ProactiveWorker:
        """Worker running the briefing, meetings, mail watch, watch rules and goal reminders."""
        jobs: list[ProactiveJob] = [
            MorningBriefingJob(sources, settings),
            MeetingPrepJob(sources, settings),
            MeetingFollowupJob(sources, settings),
            MeetingMinutesJob(sources, settings),
            MailWatchJob(sources, settings),
            WatcherJob(sources, settings),
            GoalReminderJob(settings),
            RoutineJob(sources, settings),
        ]
        return cls(session_factory, jobs, settings)

    async def run_once(self) -> int:
        """Run one tick; returns how many notifications were created."""
        now = self._clock()
        created = 0
        for user_id in await self._connected_users():
            for job in self.jobs:
                created += await self._run_job(job, user_id, now)
        await self._purge_if_due(now)
        return created

    async def run(self, stop_event: asyncio.Event) -> None:
        """Tick until ``stop_event`` is set."""
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.settings.tick_seconds)
            except TimeoutError:
                continue

    async def _connected_users(self) -> list[str]:
        async with self.session_factory() as session:
            try:
                rows = await session.scalars(
                    select(GoogleIntegration.user_id)
                    .join(User, User.id == GoogleIntegration.user_id)
                    .where(GoogleIntegration.revoked_at.is_(None), User.is_active.is_(True))
                )
                return list(rows.all())
            except Exception:  # noqa: BLE001 - a database outage must not kill the loop
                logger.exception("proactive_worker_list_users_failed")
                return []

    async def _run_job(self, job: ProactiveJob, user_id: str, now: datetime) -> int:
        key = (user_id, job.name)
        paused_until = self._paused_until.get(key)
        if paused_until is not None and now < paused_until:
            return 0
        async with self.session_factory() as session:
            try:
                notifications = await job.run(session, user_id, now)
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - one user's job must not stop the others
                await session.rollback()
                self._paused_until[key] = now + timedelta(
                    seconds=self.settings.failure_backoff_seconds
                )
                logger.warning(
                    "proactive_job_failed", job=job.name, user_id=user_id, error=str(exc)
                )
                return 0
        self._paused_until.pop(key, None)
        bus = self._bus or get_notification_bus()
        for notification in notifications:
            await bus.publish(user_id, notification.id)
            logger.info(
                "proactive_notification_created",
                job=job.name,
                user_id=user_id,
                notification_id=notification.id,
            )
        return len(notifications)

    async def _purge_if_due(self, now: datetime) -> None:
        if self._last_purge is not None and now - self._last_purge < PURGE_INTERVAL:
            return
        self._last_purge = now
        cutoff = now - timedelta(days=self.settings.notification_retention_days)
        async with self.session_factory() as session:
            try:
                await NotificationService.purge_older_than(session, cutoff)
                await session.commit()
            except Exception:  # noqa: BLE001 - purge is housekeeping, never fatal
                await session.rollback()
                logger.exception("proactive_notification_purge_failed")
