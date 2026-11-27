"""Owner-bound storage of goals and watch rules."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Goal, WatchRule
from app.services.platform.naming import normalized_name
from app.services.skills.matching import unaccent_vietnamese

MAX_ACTIVE_GOALS = 20
MAX_ACTIVE_WATCHES = 20


class GoalService:
    """Create goals, report progress and tick steps; every query is scoped to the owner."""

    @staticmethod
    async def create(
        session: AsyncSession,
        user_id: str,
        title: str,
        due: date | None,
        steps: list[dict[str, Any]],
    ) -> Goal:
        """Store a new active goal with its planned steps."""
        goal = Goal(user_id=user_id, title=title[:255], due=due, steps=steps)
        session.add(goal)
        await session.flush()
        return goal

    @staticmethod
    async def active(session: AsyncSession, user_id: str) -> list[Goal]:
        rows = await session.scalars(
            select(Goal)
            .where(Goal.user_id == user_id, Goal.status == "active")
            .order_by(Goal.created_at.desc())
            .limit(MAX_ACTIVE_GOALS)
        )
        return list(rows.all())

    @staticmethod
    def find(goals: list[Goal], name: str) -> list[Goal]:
        """Goals whose title contains every word of ``name`` (all goals when empty)."""
        words = unaccent_vietnamese(name).split()
        return [goal for goal in goals if all(w in unaccent_vietnamese(goal.title) for w in words)]

    @staticmethod
    async def complete_step(session: AsyncSession, goal: Goal, index: int) -> bool:
        """Tick step ``index`` (1-based); finishing the last open step closes the goal."""
        if not 1 <= index <= len(goal.steps):
            return False
        steps = [dict(step) for step in goal.steps]
        steps[index - 1]["done"] = True
        goal.steps = steps
        if all(step.get("done") for step in steps):
            goal.status = "done"
        await session.flush()
        return True


class WatchService:
    """Create, list and stop watch rules for one user."""

    @staticmethod
    async def create(
        session: AsyncSession,
        user_id: str,
        kind: str,
        target: str,
        *,
        expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> WatchRule:
        """Create a watch rule, or return the identical active one that already exists."""
        existing = await session.scalar(
            select(WatchRule).where(
                WatchRule.user_id == user_id,
                WatchRule.kind == kind,
                WatchRule.target == target,
                WatchRule.active.is_(True),
            )
        )
        if existing is not None:
            return existing
        rule = WatchRule(
            user_id=user_id,
            kind=kind,
            target=target[:255],
            expires_at=expires_at,
            last_checked_at=now,
        )
        session.add(rule)
        await session.flush()
        return rule

    @staticmethod
    async def active(session: AsyncSession, user_id: str | None = None) -> list[WatchRule]:
        """Active watch rules, of one user or (for the worker) of everyone."""
        stmt = select(WatchRule).where(WatchRule.active.is_(True))
        if user_id is not None:
            stmt = stmt.where(WatchRule.user_id == user_id)
        rows = await session.scalars(stmt.order_by(WatchRule.created_at).limit(500))
        return list(rows.all())

    @staticmethod
    async def stop(session: AsyncSession, user_id: str, target: str) -> list[WatchRule]:
        """Deactivate the user's rules whose target is exactly ``target`` (all when empty).

        Targets are compared with :func:`normalized_name`, never as substrings, so
        "dừng theo dõi a" cannot stop every rule containing an "a". Rules with the same
        target but a different kind (mail from vs. document topic) are stopped together,
        since the user named that target. No match stops nothing.
        """
        rules = await WatchService.active(session, user_id)
        wanted = normalized_name(target)
        stopped = [rule for rule in rules if not wanted or normalized_name(rule.target) == wanted]
        for rule in stopped:
            rule.active = False
        await session.flush()
        return stopped
