"""Persistence for ``document_relations`` (typed relations per document version)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models import Document, DocumentProfile, DocumentRelation
from app.services.ingestion.relations.types import RELATIONS_EXTRACTOR_VERSION, RelationDraft


@dataclass(frozen=True)
class RelationTarget:
    """An active, profiled document version whose relations can be (re)extracted."""

    document_id: str
    logical_document_id: str
    filename: str
    category: str
    entities: list[dict[str, Any]]


class SqlAlchemyRelationRepository:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker

    async def replace(
        self,
        session: AsyncSession,
        *,
        document_id: str,
        logical_document_id: str,
        drafts: list[RelationDraft],
    ) -> None:
        """Make ``drafts`` the complete relation set of this version."""
        await session.execute(
            delete(DocumentRelation).where(DocumentRelation.document_id == document_id)
        )
        session.add_all(
            DocumentRelation(
                document_id=document_id,
                logical_document_id=logical_document_id,
                relation_type=d.relation_type.value,
                target_kind=d.target_kind.value,
                target_text=d.target_text,
                target_norm=d.target_norm,
                target_number_norm=d.target_number_norm,
                target_date=d.target_date,
                attributes=dict(d.attributes),
                evidence=d.evidence,
                source=d.source,
                extractor_version=RELATIONS_EXTRACTOR_VERSION,
            )
            for d in drafts
        )

    async def replace_standalone(self, **kwargs: Any) -> None:
        async with self._session_maker.begin() as session:
            await self.replace(session, **kwargs)

    async def targets(self, *, stale_only: bool) -> list[RelationTarget]:
        """Active profiled versions; with ``stale_only`` only those never extracted
        by the current extractor version."""
        async with self._session_maker() as session:
            stmt = (
                select(
                    Document.id,
                    Document.logical_document_id,
                    Document.title,
                    DocumentProfile.doc_category,
                    DocumentProfile.entities,
                )
                .join(DocumentProfile, DocumentProfile.document_id == Document.id)
                .where(Document.is_active.is_(True))
                .order_by(Document.title)
            )
            rows = (await session.execute(stmt)).all()
            current: set[str] = set()
            if stale_only:
                current = set(
                    (
                        await session.execute(
                            select(DocumentRelation.document_id)
                            .where(
                                DocumentRelation.extractor_version == RELATIONS_EXTRACTOR_VERSION
                            )
                            .distinct()
                        )
                    ).scalars()
                )
        return [
            RelationTarget(r.id, r.logical_document_id, r.title, r.doc_category, list(r.entities))
            for r in rows
            if r.id not in current
        ]
