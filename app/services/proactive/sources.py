"""Where proactive jobs read Google and the knowledge base from, as one injectable seam."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class ProactiveSources(Protocol):
    """Per-user read access a proactive job needs.

    Jobs only read: calendar events, mail and internal documents. Anything that
    writes to Google still has to go through an approval request.
    """

    async def calendar(self, session: AsyncSession, user_id: str) -> Any: ...

    async def communication(self, session: AsyncSession, user_id: str) -> Any: ...

    async def retrieve(self, topic: str, user_id: str) -> Any: ...


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
