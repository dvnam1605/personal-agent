"""Owner-bound storage of routines and the rule for when one is due."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Routine
from app.services.context.personal_memory import parse_memory_command
from app.services.routines.commands import parse_routine_command
from app.services.skills.matching import unaccent_vietnamese

MAX_ACTIVE_ROUTINES = 10
CATCH_UP = timedelta(hours=2)


class RoutineError(ValueError):
    """A routine that cannot be saved; the message is shown to the user."""


class RoutineService:
    """Create, list and delete routines; decide which are due."""

    @staticmethod
    async def create(
        session: AsyncSession, user_id: str, query: str, weekdays: tuple[int, ...], run_time: time
    ) -> Routine:
        if parse_routine_command(query) is not None or parse_memory_command(query) is not None:
            raise RoutineError("Quy trình không được tự tạo quy trình hay sửa bộ nhớ.")
        if len(await RoutineService.active(session, user_id)) >= MAX_ACTIVE_ROUTINES:
            raise RoutineError(f"Tối đa {MAX_ACTIVE_ROUTINES} quy trình; xóa bớt rồi tạo lại.")
        routine = Routine(
            user_id=user_id,
            name=" ".join(query.split())[:120],
            query=query[:2000],
            weekdays=",".join(str(day) for day in weekdays),
            run_time=run_time,
        )
        session.add(routine)
        await session.flush()
        return routine

    @staticmethod
    async def active(session: AsyncSession, user_id: str) -> list[Routine]:
        rows = await session.scalars(
            select(Routine)
            .where(Routine.user_id == user_id, Routine.active.is_(True))
            .order_by(Routine.created_at)
        )
        return list(rows.all())

    @staticmethod
    async def delete(session: AsyncSession, user_id: str, target: str) -> list[Routine]:
        """Deactivate routines named by number ("1") or by words of their request."""
        routines = await RoutineService.active(session, user_id)
        if target.strip().isdigit():
            index = int(target) - 1
            chosen = [routines[index]] if 0 <= index < len(routines) else []
        else:
            words = unaccent_vietnamese(target).split()
            chosen = [r for r in routines if all(w in unaccent_vietnamese(r.query) for w in words)]
        for routine in chosen:
            routine.active = False
        await session.flush()
        return chosen

    @staticmethod
    def days(routine: Routine) -> tuple[int, ...]:
        return tuple(int(day) for day in routine.weekdays.split(",") if day.strip().isdigit())

    @staticmethod
    def is_due(routine: Routine, local_now: datetime) -> bool:
        """Due on a scheduled day from its time until ``CATCH_UP`` later, once per day."""
        today: date = local_now.date()
        if routine.last_run_on == today or today.weekday() not in RoutineService.days(routine):
            return False
        start = datetime.combine(today, routine.run_time, tzinfo=local_now.tzinfo)
        return start <= local_now < start + CATCH_UP
