"""Goal reminder job: each morning, remind the user of goal steps due today or overdue."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.proactive import ProactiveSettings
from app.infrastructure.db.models import Notification
from app.services.goals.service import GoalService
from app.services.proactive.formatting import plain
from app.services.proactive.notifications import NotificationService

GOAL_KIND = "goal_reminder"


class GoalReminderJob:
    """One reminder per goal per day listing its open steps that are due or late.

    It starts at the morning briefing time, so reminders arrive together with the
    briefing; a goal with nothing due that day stays quiet.
    """

    name = "goal_reminder"

    def __init__(self, settings: ProactiveSettings) -> None:
        self._settings = settings
        self._zone = ZoneInfo(settings.timezone)

    async def run(self, session: AsyncSession, user_id: str, now: datetime) -> list[Notification]:
        local = now.astimezone(self._zone)
        start = datetime.combine(local.date(), self._settings.briefing_time, tzinfo=self._zone)
        if local < start:
            return []
        today = local.date()
        created: list[Notification] = []
        for goal in await GoalService.active(session, user_id):
            due_steps: list[tuple[int, str, date]] = []
            for index, step in enumerate(goal.steps, start=1):
                step_due = _due(step)
                if not step.get("done") and step_due is not None and step_due <= today:
                    due_steps.append((index, str(step.get("title") or ""), step_due))
            if not due_steps:
                continue
            lines = [f"Mục tiêu **{plain(goal.title)}** có bước cần làm:"]
            for index, title, step_due in due_steps:
                late = " — quá hạn" if step_due < today else ""
                lines.append(f"- Bước {index}: {plain(title)}{late}")
            lines.append("")
            lines.append("Xong bước nào, nói “xong bước N của mục tiêu …”.")
            notification = await NotificationService.create(
                session,
                user_id=user_id,
                kind=GOAL_KIND,
                title=f"Mục tiêu: {plain(goal.title)} — {len(due_steps)} bước đến hạn",
                body="\n".join(lines),
                dedup_key=f"goal:{goal.id}:{today.isoformat()}",
                data={"goal_id": goal.id, "steps": [index for index, _, _ in due_steps]},
            )
            if notification is not None:
                created.append(notification)
        return created


def _due(step: dict[str, object]) -> date | None:
    try:
        return date.fromisoformat(str(step.get("due")))
    except ValueError:
        return None
