"""SQLAlchemy adapter for the ingestion persistence ports (spec P9D-2).

Writes run inside the orchestrator-owned transaction: candidate version +
chunk rows insert, previous ACTIVE versions archive, and the candidate flips
ACTIVE in one commit — a failure anywhere leaves the previous searchable
version untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.chunks import ChildChunkDraft, ChunkLevel, ParentChunkDraft
from app.domain.models.documents import SourceDocument
from app.domain.models.ingestion import StoredFingerprintState
from app.infrastructure.db.models import Document, DocumentChunk

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence


class SqlAlchemyUnitOfWork:
    """UnitOfWork adapter over an ``async_sessionmaker`` (review: live-PG fix).

    ``async_sessionmaker`` exposes ``begin()`` but not the port's
    ``transaction()``; this adapter bridges the two without leaking
    SQLAlchemy types into the orchestrator.
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker

    def transaction(self):
        return self._session_maker.begin()


class SqlAlchemyIngestionRepository:
    """Implements IngestionRepository on PostgreSQL + pgvector."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker

    async def latest_state(self, logical_document_id: str) -> StoredFingerprintState | None:
        async with self._session_maker() as session:
            statement = (
                select(Document)
                .where(Document.logical_document_id == logical_document_id)
                .order_by(Document.version_number.desc())
                .limit(1)
            )
            row = (await session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        # Legacy rows may carry a NULL fingerprint (pre-0006); surfacing them
        # as state-without-fingerprint makes the orchestrator reingest as a
        # new version instead of colliding at version 1 (review L1).
        return StoredFingerprintState(
            logical_document_id=row.logical_document_id,
            document_id=row.id,
            version_number=row.version_number,
            fingerprint=row.fingerprint,
            is_active=row.is_active,
        )

    async def persist_candidate(
        self,
        session: object,
        *,
        source: SourceDocument,
        logical_document_id: str,
        document_id: str,
        version_number: int,
        fingerprint: str,
        parser_name: str,
        parser_version: str,
        parents: list[ParentChunkDraft],
        children: Sequence[tuple[ChildChunkDraft, list[float] | None]],
    ) -> tuple[str, int]:
        typed = self._session(session)

        document = Document(
            id=document_id,
            user_id=None,
            logical_document_id=logical_document_id,
            version_number=version_number,
            source_type=source.source_type,
            external_id=source.source_id[:255],
            title=source.filename[:255],
            uri=source.external_uri,
            mime_type=source.mime_type,
            source_content_hash=source.checksum,
            fingerprint=fingerprint,
            status="processing",
            is_active=False,
            metadata_={
                "source_id": source.source_id,
                "parser_name": parser_name,
                "parser_version": parser_version,
                "parent_chunker_version": parents[0].parent_chunker_version if parents else None,
                "child_chunker_version": children[0][0].child_chunker_version if children else None,
                "source_metadata": _json_safe(source.metadata),
            },
        )
        typed.add(document)

        for parent in parents:
            typed.add(
                DocumentChunk(
                    id=parent.id,
                    document_id=document.id,
                    chunk_index=parent.ordinal,
                    hierarchy_level=0,
                    node_type=ChunkLevel.PARENT.value,
                    parent_id=None,
                    heading_path=list(parent.heading_path),
                    content_raw=parent.raw_text,
                    content_embedding_text=parent.raw_text,
                    page_start=parent.page_start,
                    page_end=parent.page_end,
                    token_count=parent.token_count,
                    content_hash=parent.content_hash,
                    source_block_ids=list(parent.source_block_ids),
                    parent_chunker_version=parent.parent_chunker_version,
                    metadata_={"ordinal": parent.ordinal},
                )
            )
        for child, vector in children:
            typed.add(
                DocumentChunk(
                    id=child.id,
                    document_id=document.id,
                    chunk_index=child.chunk_index,
                    hierarchy_level=1,
                    node_type=child.level.value,
                    parent_id=child.parent_id,
                    heading_path=list(child.heading_path),
                    content_raw=child.raw_text,
                    content_embedding_text=child.embedding_text,
                    page_start=child.page_start,
                    page_end=child.page_end,
                    token_count=child.token_count,
                    content_hash=child.content_hash,
                    source_block_ids=list(child.source_block_ids),
                    child_chunker_version=child.child_chunker_version,
                    embedding=vector,
                    embedding_model=_embedding_model(),
                    embedding_dimensions=len(vector) if vector else _embedding_dimensions(),
                )
            )
        await typed.flush()
        return document.id, version_number

    async def activate_candidate(
        self,
        session: object,
        *,
        logical_document_id: str,
        document_id: str,
    ) -> None:
        typed = self._session(session)
        await typed.execute(
            update(Document)
            .where(
                Document.logical_document_id == logical_document_id,
                Document.id != document_id,
                Document.is_active.is_(True),
            )
            .values(is_active=False, status="archived")
        )
        await typed.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(is_active=True, status="active")
        )

    async def deactivate_logical_document(self, session: object, logical_document_id: str) -> bool:
        typed = self._session(session)
        result = await typed.execute(
            update(Document)
            .where(
                Document.logical_document_id == logical_document_id,
                Document.status != "archived",
            )
            .values(is_active=False, status="archived")
        )
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return rowcount > 0

    @staticmethod
    def _session(session: object) -> AsyncSession:
        assert isinstance(session, AsyncSession), "expected an AsyncSession transaction"
        return session


def _embedding_model() -> str:
    from app.core.config import settings as app_settings

    return app_settings.embedding.model


def _embedding_dimensions() -> int:
    from app.core.config import settings as app_settings

    return app_settings.embedding.dimensions


def _json_safe(metadata: dict[str, Any]) -> dict[str, Any]:
    """Recursively keep JSON-serializable values; stringify the rest (review L4)."""

    def convert(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): convert(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(item) for item in value]
        return str(value)

    return {str(key): convert(value) for key, value in metadata.items()}
