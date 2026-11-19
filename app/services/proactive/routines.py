"""Routine job: run the user's saved requests on their schedule and notify the result."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.infrastructure.db.models import Notification
from app.services.proactive.formatting import plain
from app.services.proactive.notifications import NotificationService
from app.services.proactive.sources import ProactiveSources
from app.services.routines.service import RoutineService

ROUTINE_KIND = "routine"


class RoutineJob:
    """Run each due routine through the normal orchestrator, as the user asking it.

    A routine has exactly the user's own permissions: reads answer directly and a write
    it proposes waits for approval like any other. The result arrives as a notification;
    a routine runs at most once per scheduled day.
    """

    name = "routine"

    def __init__(self, sources: ProactiveSources, settings: ProactiveSettings) -> None:
        self._sources = sources
        self._zone = ZoneInfo(settings.timezone)

    async def run(self, session: AsyncSession, user_id: str, now: datetime) -> list[Notification]:
        local = now.astimezone(self._zone)
        created: list[Notification] = []
        for routine in await RoutineService.active(session, user_id):
            if not RoutineService.is_due(routine, local):
                continue
            routine.last_run_on = local.date()
            await session.flush()
            result = await self._sources.ask(session, user_id, routine.query)
            body = result.message
            if result.approval_id:
                body += "\n\nCó hành động cần bạn duyệt trong ứng dụng hoặc Telegram."
            notification = await NotificationService.create(
                session,
                user_id=user_id,
                kind=ROUTINE_KIND,
                title=f"Quy trình: {plain(routine.name, 100)}",
                body=body,
                dedup_key=f"routine:{routine.id}:{local.date().isoformat()}",
                data={"routine_id": routine.id, "approval_id": result.approval_id},
            )
            if notification is not None:
                created.append(notification)
        return created
