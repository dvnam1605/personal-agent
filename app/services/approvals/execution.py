"""Execute an approved mutation with the one-shot HMAC token."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import RunStatus
from app.domain.models import AssistantState, ToolContext, ToolInput, ToolResult
from app.infrastructure.db.models import ApprovalRequest
from app.services.platform.run_persistence import RunPersistenceService

ToolExecuteFn = Callable[[ToolInput, ToolContext], Awaitable[ToolResult]]


class ApprovalExecutionResult(BaseModel):
    """Outcome of running an approved tool after the human decision."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    tool_name: str
    output: dict[str, Any] | None = None
    error: str | None = None
    message: str


class ApprovalExecutionService:
    """Resume a waiting run and invoke the bound Calendar/Gmail tool."""

    def __init__(
        self,
        *,
        oauth_service: Any | None = None,
        calendar_execute: ToolExecuteFn | None = None,
        communication_execute: ToolExecuteFn | None = None,
    ) -> None:
        self._oauth_service = oauth_service
        self._calendar_execute = calendar_execute
        self._communication_execute = communication_execute

    async def execute(
        self,
        session: AsyncSession,
        request: ApprovalRequest,
        token: str,
        user_id: str,
    ) -> ApprovalExecutionResult:
        """Consume ``token`` and run ``request.tool_name`` with persisted parameters."""
        tool_name = (request.tool_name or "").strip()
        if not tool_name:
            return ApprovalExecutionResult(
                success=False,
                tool_name="",
                error="missing_tool",
                message="Approval này không gắn tool để thực thi.",
            )
        await self._resume_run(session, request)
        arguments = dict(request.parameters) if isinstance(request.parameters, dict) else {}
        tool_input = ToolInput(tool_name=tool_name, arguments=arguments)
        context = ToolContext(
            run_id=request.run_id,
            user_id=user_id,
            approval_token=token,
            agent_name="ApprovalExecutionService",
        )
        try:
            result = await self._dispatch(session, user_id, tool_input, context)
        except Exception as exc:  # noqa: BLE001 - convert provider failures into a tool result
            await RunPersistenceService.complete_run(
                session,
                request.run_id,
                RunStatus.FAILED,
                0.0,
                error_summary="Approved tool execution failed.",
            )
            return ApprovalExecutionResult(
                success=False,
                tool_name=tool_name,
                error=type(exc).__name__,
                message="Không thực thi được hành động đã duyệt.",
            )

        output = _jsonable(result.output)
        if result.success:
            await RunPersistenceService.complete_run(
                session, request.run_id, RunStatus.COMPLETED, 0.0
            )
            return ApprovalExecutionResult(
                success=True,
                tool_name=tool_name,
                output=output if isinstance(output, dict) else {"result": output},
                message=_success_message(tool_name, output),
            )
        await RunPersistenceService.complete_run(
            session,
            request.run_id,
            RunStatus.FAILED,
            0.0,
            error_summary=result.error or "Approved tool execution failed.",
        )
        return ApprovalExecutionResult(
            success=False,
            tool_name=tool_name,
            error=result.error,
            message=result.error or "Không thực thi được hành động đã duyệt.",
        )

    async def _dispatch(
        self,
        session: AsyncSession,
        user_id: str,
        tool_input: ToolInput,
        context: ToolContext,
    ) -> ToolResult:
        if tool_input.tool_name.startswith("calendar."):
            execute = self._calendar_execute or await self._default_calendar_execute(
                session, user_id, tool_input.tool_name
            )
            return await execute(tool_input, context)
        if tool_input.tool_name.startswith("gmail."):
            execute = self._communication_execute or await self._default_gmail_execute(
                session, user_id
            )
            return await execute(tool_input, context)
        from app.domain.models import ToolExecutionMetadata

        return ToolResult(
            tool_name=tool_input.tool_name,
            success=False,
            error=f"POST /approvals execute chưa hỗ trợ tool '{tool_input.tool_name}'.",
            metadata=ToolExecutionMetadata(tool_name=tool_input.tool_name, latency_ms=0.0),
        )

    async def _default_calendar_execute(
        self, session: AsyncSession, user_id: str, tool_name: str
    ) -> ToolExecuteFn:
        from app.api.routes.google_auth import google_oauth_service
        from app.tools.google_calendar import GoogleCalendarTools

        oauth = self._oauth_service or google_oauth_service
        tools = await GoogleCalendarTools.for_user(oauth, session, user_id, tool_name=tool_name)
        return tools.execute

    async def _default_gmail_execute(self, session: AsyncSession, user_id: str) -> ToolExecuteFn:
        from app.api.routes.google_auth import google_oauth_service
        from app.integrations.google_gmail import GMAIL_MODIFY_SCOPE
        from app.services.google.communication import CommunicationService
        from app.tools.google_communication import GoogleCommunicationTools

        oauth = self._oauth_service or google_oauth_service
        client = await oauth.create_client(session, user_id, required_scopes=[GMAIL_MODIFY_SCOPE])
        tools = GoogleCommunicationTools(CommunicationService.from_client(client))
        return tools.execute

    @staticmethod
    async def _resume_run(session: AsyncSession, request: ApprovalRequest) -> AssistantState:
        from app.services.approvals import ApprovalRequestService

        run = await RunPersistenceService.get_run(session, request.run_id)
        if run is None:
            raise ValueError(f"Run not found: {request.run_id}")
        if run.status == RunStatus.COMPLETED.value:
            raise ValueError(f"Run already completed: {request.run_id}")
        try:
            return await ApprovalRequestService.resume_approved(session, request.id)
        except ValueError as exc:
            message = str(exc).casefold()
            if "checkpoint not found" not in message and "cannot be rehydrated" not in message:
                raise
            state = AssistantState(
                run_id=request.run_id,
                user_id=run.user_id,
                request=run.request,
                status=RunStatus.RUNNING,
            )
            if run.status in {RunStatus.WAITING_APPROVAL.value, RunStatus.RUNNING.value}:
                await RunPersistenceService.update_run_state(session, request.run_id, state)
            return state


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _success_message(tool_name: str, output: Any) -> str:
    if tool_name == "calendar.create_event":
        summary = ""
        event_id = ""
        if isinstance(output, dict):
            summary = str(output.get("summary") or "")
            event_id = str(output.get("id") or "")
        label = f" '{summary}'" if summary else ""
        suffix = f" (id={event_id})" if event_id else ""
        return f"Đã tạo sự kiện{label} trên Google Calendar{suffix}."
    if tool_name == "gmail.create_draft":
        draft_id = ""
        if isinstance(output, dict):
            draft_id = str(output.get("id") or "")
        suffix = f" (id={draft_id})" if draft_id else ""
        return f"Đã tạo nháp Gmail{suffix}."
    return f"Đã thực thi {tool_name}."


__all__ = ["ApprovalExecutionResult", "ApprovalExecutionService"]
