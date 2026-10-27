"""Google Tasks tools: reads run directly, writes only with a one-shot approval token."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import AppError, PermissionDeniedError
from app.domain.models import ToolContext, ToolExecutionMetadata, ToolInput, ToolResult
from app.integrations.google_tasks import TASKS_SCOPE, TasksAdapter
from app.services.approvals.tokens import require_mutation_approval

logger = logging.getLogger(__name__)

TASKS_LIST = "tasks.list"
TASKS_CREATE = "tasks.create"
TASKS_COMPLETE = "tasks.complete"
MUTATION_TOOLS = frozenset({TASKS_CREATE, TASKS_COMPLETE})


class GoogleTasksTools:
    """Execute Tasks operations as normalized ``ToolResult`` values.

    Creating and completing a task change the user's Google account, so like every other
    Google write they require the approval token bound to these exact arguments.
    """

    def __init__(self, adapter: TasksAdapter) -> None:
        self.adapter = adapter

    @classmethod
    async def for_user(
        cls, oauth_service: Any, session: AsyncSession, user_id: str
    ) -> GoogleTasksTools:
        client = await oauth_service.create_client(session, user_id, required_scopes=[TASKS_SCOPE])
        return cls(TasksAdapter(client))

    async def execute(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        started = time.perf_counter()
        name = tool_input.tool_name
        args = tool_input.arguments
        try:
            if name in MUTATION_TOOLS:
                if context.read_only_view:
                    raise PermissionDeniedError("Read-only tool views cannot execute mutations.")
                await require_mutation_approval(
                    tool_name=name,
                    context_token=context.approval_token,
                    arguments=args,
                    run_id=context.run_id,
                    user_id=context.user_id,
                    delegation=context.delegation,
                    consume=True,
                )
            if name == TASKS_LIST:
                items = await self.adapter.list_tasks(
                    show_completed=bool(args.get("show_completed"))
                )
                output: Any = [item.model_dump(mode="json") for item in items]
            elif name == TASKS_CREATE:
                due = date.fromisoformat(str(args["due"])) if args.get("due") else None
                created = await self.adapter.insert_task(
                    str(args.get("title") or ""), notes=args.get("notes") or None, due=due
                )
                output = created.model_dump(mode="json")
            elif name == TASKS_COMPLETE:
                done = await self.adapter.complete_task(str(args.get("task_id") or ""))
                output = done.model_dump(mode="json")
            else:
                return _failure(name, "Tasks tool is not registered.", started)
            return ToolResult(
                tool_name=name,
                success=True,
                output=output,
                metadata=ToolExecutionMetadata(tool_name=name, latency_ms=_ms(started)),
            )
        except AppError as exc:
            return _failure(name, exc.message, started)
        except (KeyError, ValueError) as exc:
            return _failure(name, f"Invalid task arguments: {type(exc).__name__}", started)


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _failure(name: str, message: str, started: float) -> ToolResult:
    return ToolResult(
        tool_name=name,
        success=False,
        error=message,
        metadata=ToolExecutionMetadata(tool_name=name, latency_ms=_ms(started)),
    )
