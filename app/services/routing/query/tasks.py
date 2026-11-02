"""To-do list in the query orchestrator: list Google Tasks, propose adding or completing one."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.specialist.calendar import DEFAULT_TIMEZONE
from app.domain.enums import ActionRiskLevel, RouteType
from app.domain.errors import PermissionDeniedError
from app.domain.models import ProposedAction
from app.domain.models.routing.route import RouteDecision
from app.integrations.google_tasks import TASKS_SCOPE, TaskItem, TasksAdapter
from app.services.approvals import ApprovalRequestService
from app.services.skills.matching import unaccent_vietnamese
from app.services.tasks.commands import TaskCommand, parse_task_command
from app.tools.google_tasks import TASKS_COMPLETE, TASKS_CREATE

from .clarification import ClarificationMixin
from .models import QueryResult, route_info

MAX_LISTED = 20
TASKS_DENIED = (
    "Google từ chối truy cập Google Tasks. Thường là do Google Tasks API chưa được bật trong "
    "project Google Cloud của OAuth client (APIs & Services → Library → Google Tasks API → "
    "Enable), hoặc tài khoản chưa cấp quyền Tasks — khi đó bấm “Kết nối” lại."
)


class TaskMixin(ClarificationMixin):
    """Answers "việc cần làm", "thêm việc: …", "xong việc …" against Google Tasks.

    Listing reads directly. Adding and completing change the user's Google account, so
    they become approval requests showing exactly the title and due date that will be
    written; nothing is written before the user approves.
    """

    async def _tasks(self, session: AsyncSession, user_id: str) -> Any:
        factory = self._tasks_for_user
        if factory is not None:
            return await factory(session, user_id)
        from app.api.routes.google_auth import google_oauth_service

        oauth = self._oauth_service or google_oauth_service
        client = await oauth.create_client(session, user_id, required_scopes=[TASKS_SCOPE])
        return TasksAdapter(client)

    async def _maybe_handle_tasks(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        decision: RouteDecision,
    ) -> QueryResult | None:
        """Handle a to-do command; ``None`` when the turn is not one."""
        if decision.route_type is RouteType.REJECT:
            return None
        today = self._clock().astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()
        command = parse_task_command(query, today)
        if command is None:
            return None
        route = route_info(decision)
        if command.kind == "create":
            return await self._propose_create(session, run_id, route, command)
        try:
            adapter = await self._tasks(session, user_id)
            if command.kind == "complete":
                return await self._propose_complete(session, run_id, route, command, adapter)
            items = await adapter.list_tasks()
        except PermissionDeniedError:
            return QueryResult(
                run_id=run_id,
                status="blocked",
                message=TASKS_DENIED,
                route=route,
                data={"error_code": "PERMISSION_DENIED"},
            )
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=_list_message(items, today),
            route=route,
            data={"tasks": [item.model_dump(mode="json") for item in items[:MAX_LISTED]]},
        )

    async def _propose_create(
        self, session: AsyncSession, run_id: str, route: Any, command: TaskCommand
    ) -> QueryResult:
        due = command.due.isoformat() if command.due else None
        when = f" (hạn {command.due:%d/%m/%Y})" if command.due else ""
        proposal = ProposedAction(
            action_type="create_task",
            description=f"Thêm việc “{command.title}”{when} vào Google Tasks.",
            tool_name=TASKS_CREATE,
            parameters={"title": command.title, "due": due},
            risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
            requires_approval=True,
        )
        approval = await ApprovalRequestService.create_request(session, run_id, proposal)
        return QueryResult(
            run_id=run_id,
            status="needs_approval",
            message=f"{proposal.description} Bấm **Duyệt** để thêm.",
            route=route,
            data={
                "proposal": proposal.model_dump(mode="json"),
                "task": {"title": command.title, "due": due},
            },
            approval_id=approval.id,
        )

    async def _propose_complete(
        self,
        session: AsyncSession,
        run_id: str,
        route: Any,
        command: TaskCommand,
        adapter: Any,
    ) -> QueryResult:
        matches = _matching(await adapter.list_tasks(), command.title)
        if len(matches) != 1:
            message = (
                f"Không thấy việc nào khớp “{command.title}” trong danh sách đang mở."
                if not matches
                else "Có nhiều việc khớp, bạn nói cụ thể hơn:\n"
                + "\n".join(f"- {item.title}" for item in matches[:5])
            )
            return QueryResult(run_id=run_id, status="completed", message=message, route=route)
        task = matches[0]
        proposal = ProposedAction(
            action_type="complete_task",
            description=f"Đánh dấu xong việc “{task.title}” trên Google Tasks.",
            tool_name=TASKS_COMPLETE,
            parameters={"task_id": task.id, "title": task.title},
            risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
            requires_approval=True,
        )
        approval = await ApprovalRequestService.create_request(session, run_id, proposal)
        return QueryResult(
            run_id=run_id,
            status="needs_approval",
            message=f"{proposal.description} Bấm **Duyệt** để xác nhận.",
            route=route,
            data={"proposal": proposal.model_dump(mode="json")},
            approval_id=approval.id,
        )


def _matching(items: list[TaskItem], title: str) -> list[TaskItem]:
    words = [word for word in unaccent_vietnamese(title).split() if len(word) > 1]
    return [
        item
        for item in items
        if not item.is_done and all(word in unaccent_vietnamese(item.title) for word in words)
    ]


def _list_message(items: list[TaskItem], today: Any) -> str:
    open_items = [item for item in items if not item.is_done]
    if not open_items:
        return "Bạn không còn việc nào đang mở trong Google Tasks."
    open_items.sort(key=lambda item: (item.due is None, item.due or today))
    lines = [f"Bạn còn {len(open_items)} việc:"]
    for item in open_items[:MAX_LISTED]:
        if item.due is None:
            suffix = ""
        elif item.due < today:
            suffix = f" — **quá hạn** ({item.due:%d/%m})"
        elif item.due == today:
            suffix = " — hạn hôm nay"
        else:
            suffix = f" — hạn {item.due:%d/%m}"
        lines.append(f"- {item.title}{suffix}")
    return "\n".join(lines)
