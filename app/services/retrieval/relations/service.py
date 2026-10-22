"""Answer relation questions from ``document_relations`` with SQL.

Visibility mirrors the metadata route: only active versions the requester
may see (own or shared). Returns ``None`` whenever there is no plan or no
matching fact, so the caller falls back to the metadata / RAG routes and a
relation miss can never hide an answer that lives in the text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.retrieval.citation import Citation
from app.infrastructure.db.models import Document, DocumentProfile, DocumentRelation
from app.services.ingestion.profiling.rules import normalize_doc_number
from app.services.ingestion.relations.types import RelationType
from app.services.retrieval.relations.planner import RelationPlan, RelationQueryPlanner
from app.services.retrieval.relations.render import render_relation_answer

STATEMENT_TIMEOUT_MS = 3000
CATALOG_TTL_SECONDS = 300.0
MAX_DOCUMENTS = 30


@dataclass(frozen=True)
class RelationAnswer:
    answer: str  # deterministic fact draft (also the fallback answer)
    citations: list[Citation]
    total: int
    trace: dict[str, Any]
    # The orchestrator requires every fact target to appear in an LLM answer.
    facts: list[dict[str, Any]] = field(default_factory=list)


def _number_condition(column: Any, raw: str) -> Any:
    """Exact normalised match; a bare "1119" matches 1119QDTNVN but not 11190…."""
    norm = normalize_doc_number(raw) or ""
    if not norm.isdigit():
        return column == norm
    next_char = func.substr(column, len(norm) + 1, 1)
    return or_(
        column == norm,
        and_(column.like(f"{norm}%"), next_char.not_in(list("0123456789"))),
    )


def _number_matches(stored: str, raw: str) -> bool:
    norm = normalize_doc_number(raw) or ""
    if stored == norm:
        return True
    return (
        norm.isdigit()
        and stored.startswith(norm)
        and not stored[len(norm) : len(norm) + 1].isdigit()
    )


class RelationLookupService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        planner: RelationQueryPlanner | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._planner = planner or RelationQueryPlanner()
        self._catalog: dict[str | None, tuple[float, list[str], list[str]]] = {}

    def _sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            from app.infrastructure.db.session import get_session_factory

            self._session_factory = get_session_factory()
        return self._session_factory

    def invalidate(self) -> None:
        self._catalog.clear()

    @staticmethod
    def _visible(requester_id: str | None) -> list[Any]:
        owner = (
            Document.user_id.is_(None)
            if requester_id is None
            else or_(Document.user_id == requester_id, Document.user_id.is_(None))
        )
        return [Document.is_active.is_(True), owner]

    async def _known(self, requester_id: str | None) -> tuple[list[str], list[str]]:
        """``(target names, own document numbers)`` visible to the requester."""
        cached = self._catalog.get(requester_id)
        if cached and cached[0] > time.monotonic():
            return cached[1], cached[2]
        visible = self._visible(requester_id)
        names_stmt = (
            select(DocumentRelation.target_norm)
            .join(Document, Document.id == DocumentRelation.document_id)
            .where(*visible, DocumentRelation.target_kind != "document")
            .distinct()
        )
        numbers_stmt = (
            select(DocumentProfile.doc_number_norm)
            .join(Document, Document.id == DocumentProfile.document_id)
            .where(*visible, DocumentProfile.doc_number_norm.is_not(None))
        )
        async with self._sessions()() as session:
            names = list((await session.execute(names_stmt)).scalars())
            numbers = [n for n in (await session.execute(numbers_stmt)).scalars() if n is not None]
        self._catalog[requester_id] = (time.monotonic() + CATALOG_TTL_SECONDS, names, numbers)
        return names, numbers

    async def plan(
        self, question: str, requester_id: str | None, *, today: date | None = None
    ) -> RelationPlan | None:
        names, numbers = await self._known(requester_id)
        return self._planner.plan(
            question,
            today=today,
            known_names=names,
            is_source_number=lambda raw: any(_number_matches(n, raw) for n in numbers),
        )

    async def answer(
        self, question: str, requester_id: str | None, *, today: date | None = None
    ) -> RelationAnswer | None:
        plan = await self.plan(question, requester_id, today=today)
        if plan is None:
            return None
        return await self.run(plan, requester_id)

    async def run(self, plan: RelationPlan, requester_id: str | None) -> RelationAnswer | None:
        types = [t.value for t in plan.types]
        # For "ai ký" combined with another relation, the other one selects documents.
        topic = [t for t in types if t != RelationType.SIGNED_BY.value] or types
        visible = self._visible(requester_id)
        started = time.perf_counter()

        source_numbers = [n.raw for n in plan.numbers if n.is_source]
        target_numbers = [n.raw for n in plan.numbers if not n.is_source]

        doc_conds: list[Any] = [*visible]
        if plan.date_from:
            doc_conds.append(DocumentProfile.doc_date >= plan.date_from)
        if plan.date_to:
            doc_conds.append(DocumentProfile.doc_date <= plan.date_to)

        def has_relation(*conds: Any) -> Any:
            return (
                select(DocumentRelation.id)
                .where(DocumentRelation.document_id == Document.id, *conds)
                .exists()
            )

        for raw in source_numbers:
            doc_conds.append(_number_condition(DocumentProfile.doc_number_norm, raw))
        for raw in target_numbers:
            doc_conds.append(
                has_relation(
                    DocumentRelation.relation_type.in_(types),
                    _number_condition(DocumentRelation.target_number_norm, raw),
                )
            )
        for name in plan.names:
            doc_conds.append(
                has_relation(
                    DocumentRelation.relation_type.in_(types),
                    DocumentRelation.target_norm == name,
                )
            )
        if not plan.anchored:
            doc_conds.append(has_relation(DocumentRelation.relation_type.in_(topic)))

        docs_stmt = (
            select(
                Document.id.label("document_id"),
                func.coalesce(DocumentProfile.title, Document.title).label("title"),
                DocumentProfile.doc_number,
                DocumentProfile.doc_date,
            )
            .join(DocumentProfile, DocumentProfile.document_id == Document.id)
            .where(and_(*doc_conds))
            .order_by(
                DocumentProfile.doc_date.is_(None), DocumentProfile.doc_date.desc(), Document.title
            )
        )
        async with self._sessions()() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                await session.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
            docs = [dict(r._mapping) for r in (await session.execute(docs_stmt)).all()]
            if not docs:
                return None
            ids = [d["document_id"] for d in docs[:MAX_DOCUMENTS]]
            fact_conds: list[Any] = [
                DocumentRelation.document_id.in_(ids),
                DocumentRelation.relation_type.in_(types),
            ]
            # An anchored relation type shows only its matching facts.
            anchor_conds = [
                _number_condition(DocumentRelation.target_number_norm, raw)
                for raw in target_numbers
            ] + [DocumentRelation.target_norm == name for name in plan.names]
            if anchor_conds:
                anchored_types = set(
                    (
                        await session.execute(
                            select(DocumentRelation.relation_type)
                            .where(DocumentRelation.document_id.in_(ids), or_(*anchor_conds))
                            .distinct()
                        )
                    ).scalars()
                )
                fact_conds.append(
                    or_(DocumentRelation.relation_type.not_in(anchored_types), or_(*anchor_conds))
                )
            facts_stmt = (
                select(
                    DocumentRelation.document_id,
                    DocumentRelation.relation_type,
                    DocumentRelation.target_text,
                    DocumentRelation.attributes,
                )
                .where(*fact_conds)
                .order_by(DocumentRelation.document_id, DocumentRelation.created_at)
            )
            facts = [dict(r._mapping) for r in (await session.execute(facts_stmt)).all()]
        if not facts:
            return None
        answer, citations = render_relation_answer(plan, docs[:MAX_DOCUMENTS], facts, len(docs))
        return RelationAnswer(
            answer=answer,
            citations=citations,
            total=len(docs),
            facts=facts,
            trace={
                "types": types,
                "source_numbers": source_numbers,
                "target_numbers": target_numbers,
                "names": list(plan.names),
                "documents": len(docs),
                "facts": len(facts),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
