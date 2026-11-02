"""Long-running goals and watch rules in the query orchestrator."""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.specialist.calendar import DEFAULT_TIMEZONE
from app.domain.enums import RouteType
from app.domain.models.routing.route import RouteDecision
from app.infrastructure.db.models import Goal
from app.services.goals.commands import GoalCommand, parse_goal_command
from app.services.goals.planner import plan_steps
from app.services.goals.service import MAX_ACTIVE_WATCHES, GoalService, WatchService

from .models import QueryResult, route_info
from .tasks import TaskMixin

_WATCH_LABELS = {
    "mail_from": "email từ",
    "mail_keyword": "email nhắc đến",
    "document_topic": "tài liệu mới về",
}


class GoalMixin(TaskMixin):
    """Plan-and-execute over days: goals broken into dated steps, and things to watch.

    A goal is planned once (model plan clamped to the deadline, generic plan without a
    model) and stored locally; the proactive worker reminds the user of due steps and
    the chat reports progress and ticks steps. Watch rules only ever produce
    notifications, so creating one needs no approval.
    """

    async def _maybe_handle_goals(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        decision: RouteDecision,
    ) -> QueryResult | None:
        """Handle a goal or watch command; ``None`` when the turn is not one."""
        if decision.route_type is RouteType.REJECT:
            return None
        now = self._clock()
        today = now.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()
        command = parse_goal_command(query, today)
        if command is None:
            return None
        message, data = await self._run_goal_command(session, user_id, command, today, now)
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=message,
            route=route_info(decision),
            data=data,
        )

    async def _run_goal_command(
        self, session: AsyncSession, user_id: str, command: GoalCommand, today: date, now: Any
    ) -> tuple[str, dict[str, Any]]:
        if command.kind == "goal_create":
            planner = self._goal_planner or plan_steps
            steps = await planner(command.title, today, command.due)
            goal = await GoalService.create(session, user_id, command.title, command.due, steps)
            return _goal_created(goal), {"goal_id": goal.id, "steps": steps}
        if command.kind == "goal_progress":
            goals = await GoalService.active(session, user_id)
            return _progress(goals, today), {"goals": [goal.id for goal in goals]}
        if command.kind == "goal_step_done":
            goals = GoalService.find(await GoalService.active(session, user_id), command.title)
            if len(goals) != 1:
                hint = (
                    "Không có mục tiêu nào khớp."
                    if not goals
                    else "Có nhiều mục tiêu, nói rõ tên mục tiêu."
                )
                return hint, {}
            goal = goals[0]
            if not await GoalService.complete_step(session, goal, command.step or 0):
                return f"Mục tiêu “{goal.title}” không có bước {command.step}.", {}
            suffix = " Mục tiêu đã hoàn thành!" if goal.status == "done" else ""
            return f"Đã đánh dấu xong bước {command.step} của “{goal.title}”.{suffix}", {
                "goal_id": goal.id
            }
        if command.kind == "watch_create" and command.watch_kind is not None:
            if len(await WatchService.active(session, user_id)) >= MAX_ACTIVE_WATCHES:
                return "Bạn đang theo dõi quá nhiều mục. Dừng bớt rồi thêm lại.", {}
            rule = await WatchService.create(
                session, user_id, command.watch_kind, command.target, now=now
            )
            label = _WATCH_LABELS[command.watch_kind]
            return (
                f"Đã bật theo dõi {label} “{rule.target}”. Tôi sẽ báo trong chuông thông báo; "
                "tôi chỉ báo, không tự làm gì.",
                {"watch_id": rule.id},
            )
        if command.kind == "watch_list":
            rules = await WatchService.active(session, user_id)
            if not rules:
                return "Bạn chưa theo dõi gì. Ví dụ: “báo tôi khi nam@congty.vn trả lời”.", {}
            lines = ["Đang theo dõi:"]
            lines.extend(
                f"- {_WATCH_LABELS.get(rule.kind, rule.kind)} “{rule.target}”" for rule in rules
            )
            return "\n".join(lines), {"watches": [rule.id for rule in rules]}
        stopped = await WatchService.stop(session, user_id, command.target)
        if not stopped:
            return "Không có mục theo dõi nào khớp.", {}
        return "Đã dừng theo dõi: " + ", ".join(f"“{rule.target}”" for rule in stopped), {}


def _goal_created(goal: Goal) -> str:
    due = f" (hạn {goal.due:%d/%m/%Y})" if goal.due else ""
    lines = [f"Đã đặt mục tiêu “{goal.title}”{due}. Kế hoạch:"]
    for index, step in enumerate(goal.steps, start=1):
        lines.append(f"{index}. {step['title']} — hạn {date.fromisoformat(step['due']):%d/%m}")
    lines.append("")
    lines.append("Tôi sẽ nhắc mỗi sáng khi có bước đến hạn. Xong bước nào, nói “xong bước N”.")
    return "\n".join(lines)


def _progress(goals: list[Goal], today: date) -> str:
    if not goals:
        return "Bạn chưa có mục tiêu nào. Ví dụ: “đặt mục tiêu: chuẩn bị hội thảo trước 15/12”."
    lines: list[str] = []
    for goal in goals:
        done = sum(1 for step in goal.steps if step.get("done"))
        lines.append(f"**{goal.title}** — {done}/{len(goal.steps)} bước")
        for index, step in enumerate(goal.steps, start=1):
            mark = (
                "✅"
                if step.get("done")
                else ("⚠️" if date.fromisoformat(step["due"]) < today else "⬜")
            )
            lines.append(
                f"{mark} {index}. {step['title']} — hạn {date.fromisoformat(step['due']):%d/%m}"
            )
        lines.append("")
    return "\n".join(lines).strip()
