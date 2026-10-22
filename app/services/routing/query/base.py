"""State and run bookkeeping shared by every part of the query orchestrator.

The orchestrator is built as a chain — OrchestratorBase → FollowupMixin →
KnowledgeMixin → SpecialistMixin → StreamMixin → QueryOrchestrator — so each
part sees the attributes and methods of the parts before it.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import RunStatus
from app.infrastructure.db.models import User
from app.services.platform.run_persistence import RunPersistenceService
from app.services.routing.triage import FastTriage

from .models import (
    CalendarFactory,
    ClockFn,
    CommunicationFactory,
    RetrieveFn,
    RunIdFn,
    SummarizeEmailsFn,
)


class OrchestratorBase:
    """Injected services and the run lifecycle helpers."""

    def __init__(
        self,
        *,
        triage: FastTriage | None = None,
        calendar_for_user: CalendarFactory | None = None,
        communication_for_user: CommunicationFactory | None = None,
        retrieve: RetrieveFn | None = None,
        oauth_service: Any | None = None,
        clock: ClockFn | None = None,
        new_run_id: RunIdFn | None = None,
        summarize_emails_fn: SummarizeEmailsFn | None = None,
        metadata_service: Any | None = None,
        relation_service: Any | None = None,
        phrase_structured: bool = True,
        pipeline: Any | None = None,
    ) -> None:
        self._metadata_service = metadata_service
        self._relation_service = relation_service
        # SQL answers are reworded by one short LLM call (facts verified).
        self._phrase_structured = phrase_structured
        self._document_service: Any | None = None
        self._triage = triage or FastTriage()
        self._calendar_for_user = calendar_for_user
        self._communication_for_user = communication_for_user
        self._retrieve = retrieve
        self._oauth_service = oauth_service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_run_id = new_run_id or (lambda: str(uuid.uuid4()))
        self._summarize_emails_fn = summarize_emails_fn
        self._pipeline: Any | None = pipeline

    @staticmethod
    async def _ensure_user(session: AsyncSession, user_id: str) -> None:
        if await session.get(User, user_id) is not None:
            return
        collision = await session.scalar(
            select(User.id).where(User.email == f"{user_id}@users.local")
        )
        email = (
            f"{user_id}@users.local"
            if collision is None
            else f"{user_id}-{uuid.uuid4().hex[:8]}@users.local"
        )
        session.add(User(id=user_id, email=email))
        await session.flush()

    @staticmethod
    async def _complete(
        session: AsyncSession,
        run_id: str,
        started: float,
        *,
        status: RunStatus,
        error_summary: str | None = None,
    ) -> None:
        latency_ms = (time.perf_counter() - started) * 1000.0
        await RunPersistenceService.complete_run(
            session, run_id, status, latency_ms, error_summary=error_summary
        )
