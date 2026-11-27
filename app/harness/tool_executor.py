"""Live tool executor for the agentic harness: read-only tools only.

Specialists call tools through ``ToolExecutor.execute``. This executor routes a call
by its prefix to the same tool classes the rest of the app uses (retrieval, web,
Calendar, Gmail, Drive, Tasks, ask-user) for the user named in the ``ToolContext``.

Writes never happen here. A mutation tool is refused outright even if a caller
forgot to hide it: an agent that wants to send mail or create an event returns a
proposed action, the bridge turns it into an approval request, and the approved
action is executed later by ``ApprovalExecutionService`` — the one write path.

Each call opens its own database session, because the supervisor runs specialist
tasks in parallel and an ``AsyncSession`` must not be shared between them.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import AppError
from app.domain.models.platform.tool import (
    ToolContext,
    ToolDefinition,
    ToolExecutionMetadata,
    ToolInput,
    ToolResult,
)
from app.tools.ask_user import ASK_USER_TOOL_NAME
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

ToolExecuteFn = Callable[[ToolInput, ToolContext], Awaitable[ToolResult]]
ToolFactory = Callable[[AsyncSession, str, str], Awaitable[ToolExecuteFn]]


def _failure(tool_name: str, error: str) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        success=False,
        error=error,
        metadata=ToolExecutionMetadata(tool_name=tool_name, latency_ms=0.0),
    )


class LiveToolExecutor:
    """Route read-only tool calls to live providers, one DB session per call.

    ``factories`` maps a tool-name prefix (``"calendar"``, ``"retrieval"`` …) to a
    coroutine that builds the provider's ``execute`` for ``(session, user_id,
    tool_name)``; tests inject fakes, production uses ``default_tool_factories``.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        factories: dict[str, ToolFactory],
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory
        self._factories = factories

    async def execute(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Run one read-only tool for ``context.user_id``; failures become results."""
        name = tool_input.tool_name
        if name in (ASK_USER_TOOL_NAME, "ask_user"):
            from app.tools.ask_user import execute_ask_user

            return await execute_ask_user(tool_input, context)
        definition = self._definition(name)
        if definition is None:
            return _failure(name, f"Tool '{name}' is not registered.")
        if definition.is_mutation:
            return _failure(
                name,
                f"Tool '{name}' changes data and cannot run here; propose it for approval instead.",
            )
        factory = self._factories.get(name.split(".", 1)[0])
        if factory is None:
            return _failure(name, f"No live provider is wired for tool '{name}'.")
        async with self._session_factory() as session:
            try:
                execute = await factory(session, context.user_id, name)
                result = await execute(tool_input, context)
                await session.commit()
            except AppError as exc:
                await session.rollback()
                logger.warning("harness_tool_failed tool=%s code=%s", name, exc.code)
                return _failure(name, exc.message)
        return result

    def _definition(self, name: str) -> ToolDefinition | None:
        try:
            return self._registry.get(name)
        except AppError:
            return None


def default_tool_factories(oauth_service: Any | None = None) -> dict[str, ToolFactory]:
    """Production providers, built lazily per call for the calling user."""
    pipeline_holder: dict[str, Any] = {}

    def _oauth() -> Any:
        if oauth_service is not None:
            return oauth_service
        from app.api.routes.google_auth import google_oauth_service

        return google_oauth_service

    async def retrieval(session: AsyncSession, user_id: str, tool_name: str) -> ToolExecuteFn:
        del session, user_id, tool_name
        from app.services.retrieval.factory import build_retrieval_pipeline
        from app.tools.knowledge import RetrievalTools

        if "pipeline" not in pipeline_holder:
            pipeline_holder["pipeline"] = build_retrieval_pipeline(use_viranker=False)
        return RetrievalTools(pipeline_holder["pipeline"]).execute

    async def web(session: AsyncSession, user_id: str, tool_name: str) -> ToolExecuteFn:
        del session, user_id, tool_name
        from app.core.config import settings
        from app.integrations.tavily import TavilyWebSearchProvider
        from app.tools.knowledge import WebSearchTools

        return WebSearchTools(TavilyWebSearchProvider(settings.web_search)).execute

    async def calendar(session: AsyncSession, user_id: str, tool_name: str) -> ToolExecuteFn:
        from app.tools.google_calendar import GoogleCalendarTools

        tools = await GoogleCalendarTools.for_user(_oauth(), session, user_id, tool_name=tool_name)
        return tools.execute

    async def gmail(session: AsyncSession, user_id: str, tool_name: str) -> ToolExecuteFn:
        del tool_name
        from app.tools.google_communication import GoogleCommunicationTools

        tools = await GoogleCommunicationTools.for_user(_oauth(), session, user_id)
        return tools.execute

    async def drive(session: AsyncSession, user_id: str, tool_name: str) -> ToolExecuteFn:
        from app.tools.google_drive import GoogleDriveTools

        tools = await GoogleDriveTools.for_user(_oauth(), session, user_id, tool_name=tool_name)
        return tools.execute

    async def tasks(session: AsyncSession, user_id: str, tool_name: str) -> ToolExecuteFn:
        del tool_name
        from app.tools.google_tasks import GoogleTasksTools

        tools = await GoogleTasksTools.for_user(_oauth(), session, user_id)
        return tools.execute

    return {
        "retrieval": retrieval,
        "web": web,
        "calendar": calendar,
        "gmail": gmail,
        "drive": drive,
        "tasks": tasks,
    }


__all__ = ["LiveToolExecutor", "ToolFactory", "default_tool_factories"]
