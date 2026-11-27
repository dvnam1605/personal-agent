"""Where proactive jobs read Google and the knowledge base from, as one injectable seam."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class ProactiveSources(Protocol):
    """Per-user read access a proactive job needs.

    Jobs only read: calendar events, mail, tasks, Drive and internal documents. Anything that
    writes to Google still has to go through an approval request.
    """

    async def calendar(self, session: AsyncSession, user_id: str) -> Any:
        """The user's Calendar service (read scopes)."""

    async def communication(self, session: AsyncSession, user_id: str) -> Any:
        """The user's Gmail and Contacts service."""

    async def retrieve(self, topic: str, user_id: str) -> Any:
        """Search the internal documents the user may read for ``topic``."""

    async def tasks(self, session: AsyncSession, user_id: str) -> Any:
        """The user's Google Tasks tools."""

    async def drive(self, session: AsyncSession, user_id: str) -> Any:
        """The user's Drive service."""

    async def ask(self, session: AsyncSession, user_id: str, query: str) -> Any:
        """Answer ``query`` as the assistant would in chat (used by routines)."""


class OrchestratorSources:
    """Sources backed by the query orchestrator, so jobs see exactly what a query sees.

    Reusing the process-wide orchestrator keeps one retrieval pipeline (and one copy of
    the embedding model) per process and the same per-user Google clients and scopes.
    """

    def __init__(self, orchestrator: Any) -> None:
        self._orchestrator = orchestrator

    async def calendar(self, session: AsyncSession, user_id: str) -> Any:
        return await self._orchestrator._calendar(session, user_id)

    async def communication(self, session: AsyncSession, user_id: str) -> Any:
        return await self._orchestrator._communication(session, user_id)

    async def retrieve(self, topic: str, user_id: str) -> Any:
        return await self._orchestrator._run_retrieval(topic, user_id)

    async def tasks(self, session: AsyncSession, user_id: str) -> Any:
        return await self._orchestrator.tasks_adapter(session, user_id)

    async def drive(self, session: AsyncSession, user_id: str) -> Any:
        return await self._orchestrator._drive(session, user_id)

    async def ask(self, session: AsyncSession, user_id: str, query: str) -> Any:
        return await self._orchestrator.handle(session, user_id, query)
