"""Execute metadata plans against ``document_profiles``."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.text import unaccent_vietnamese
from app.domain.models.retrieval.metadata_filter import (
    KnowledgePlan,
    MetadataFilterSpec,
    MetadataOperation,
    StructuredResult,
)
from app.infrastructure.db.models import Document, DocumentProfile
from app.services.retrieval.metadata.compiler import ROW_COLUMNS, compile_count, compile_rows
from app.services.retrieval.metadata.filter_parser import KnowledgeQueryPlanner

STATEMENT_TIMEOUT_MS = 3000
CATALOG_TTL_SECONDS = 300.0


class MetadataLookupService:
    """Plans and runs count/list/latest questions; caches the name catalog."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        planner: KnowledgeQueryPlanner | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._planner = planner or KnowledgeQueryPlanner()
        self._catalog: dict[
            str | None, tuple[float, list[tuple[str, str]], list[tuple[str, str]]]
        ] = {}

    def _sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            from app.infrastructure.db.session import get_session_factory

            self._session_factory = get_session_factory()
        return self._session_factory

    def invalidate(self) -> None:
        self._catalog.clear()

    async def known_names(
        self, requester_id: str | None
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """``(people, issuers)`` as ``(folded, display)`` pairs visible to the requester."""
        cached = self._catalog.get(requester_id)
        if cached and cached[0] > time.monotonic():
            return cached[1], cached[2]
        owner = (
            Document.user_id.is_(None)
            if requester_id is None
            else (Document.user_id == requester_id) | Document.user_id.is_(None)
        )
        stmt = (
            select(DocumentProfile.entities, DocumentProfile.issuer)
            .join(Document, Document.id == DocumentProfile.document_id)
            .where(Document.is_active.is_(True), owner)
        )
        async with self._sessions()() as session:
            rows = (await session.execute(stmt)).all()
        people: dict[str, str] = {}
        issuers: dict[str, str] = {}
        for entities, issuer in rows:
            for ent in entities or []:
                name = (ent or {}).get("name")
                if name and ent.get("kind") != "party":
                    people.setdefault(unaccent_vietnamese(name), name)
            if issuer:
                issuers.setdefault(unaccent_vietnamese(issuer), issuer)
        result = (sorted(people.items()), sorted(issuers.items()))
        # Marked loaded even when empty: an empty corpus must not re-query per call.
        self._catalog[requester_id] = (time.monotonic() + CATALOG_TTL_SECONDS, *result)
        return result

    async def plan(
        self, question: str, requester_id: str | None, *, today: date | None = None
    ) -> KnowledgePlan:
        people, issuers = await self.known_names(requester_id)
        return self._planner.plan(question, today=today, known_people=people, known_issuers=issuers)

    async def run(self, spec: MetadataFilterSpec, requester_id: str | None) -> StructuredResult:
        rows_stmt = compile_rows(spec, requester_id)
        count_stmt = compile_count(spec, requester_id)
        started = time.perf_counter()
        async with self._sessions()() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                await session.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
            total = int((await session.execute(count_stmt)).scalar_one())
            rows = [dict(r._mapping) for r in (await session.execute(rows_stmt)).all()]
        return StructuredResult(
            kind=spec.operation.value,
            total=total,
            columns=ROW_COLUMNS,
            rows=tuple(_jsonable(r) for r in rows),
            trace={
                "filters": spec.model_dump(mode="json", exclude={"descriptions"}),
                "sql": str(rows_stmt.compile(compile_kwargs={"literal_binds": False})),
                "row_count": len(rows),
                "total": total,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )

    async def resolve_document_ids(
        self, spec: MetadataFilterSpec, requester_id: str | None, *, cap: int
    ) -> tuple[list[str], int]:
        """Up to ``cap`` matching document ids (newest first) and the total count."""
        narrowed = spec.model_copy(update={"operation": MetadataOperation.LIST, "limit": cap})
        result = await self.run(narrowed, requester_id)
        return [str(r["document_id"]) for r in result.rows], result.total


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()}
