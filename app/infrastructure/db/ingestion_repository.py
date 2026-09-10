"""SQLAlchemy adapter for the ingestion persistence ports (spec P9D-2).

Writes run inside the orchestrator-owned transaction: candidate version +
chunk rows insert, previous ACTIVE versions archive, and the candidate flips
ACTIVE in one commit — a failure anywhere leaves the previous searchable
version untouched.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.ingestion import StoredFingerprintState
from app.domain.models.ingestion.chunks import ChildChunkDraft, ChunkLevel, ParentChunkDraft
from app.domain.models.ingestion.documents import SourceDocument
from app.infrastructure.db.models import Document, DocumentChunk
from app.services.ingestion.chunking.identity import child_chunk_id, parent_chunk_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence


class PersistCandidateResult(tuple):
    """Result tuple (document_id, version_number) with deduplication flag."""

    document_id: str
    version_number: int
    is_deduplicated: bool

    def __new__(cls, document_id: str, version_number: int, is_deduplicated: bool = False):
        instance = super().__new__(cls, (document_id, version_number))
        instance.document_id = document_id
        instance.version_number = version_number
        instance.is_deduplicated = is_deduplicated
        return instance


def _is_postgres_session(session: AsyncSession) -> bool:
    """Return True if session is connected to PostgreSQL."""
    bind = getattr(session, "bind", None)
    if bind is None and hasattr(session, "get_bind"):
        try:
            bind = session.get_bind()
        except Exception:  # noqa: BLE001 - bind lookup is diagnostic only
            sync_session = getattr(session, "sync_session", None)
            if sync_session is not None:
                try:
                    bind = (
                        sync_session.get_bind()
                        if hasattr(sync_session, "get_bind")
                        else getattr(sync_session, "bind", None)
                    )
                except Exception:  # noqa: BLE001 - bind lookup is diagnostic only
                    bind = getattr(sync_session, "bind", None)
    if bind is not None:
        dialect = getattr(bind, "dialect", None)
        return getattr(dialect, "name", "") == "postgresql"
    return False


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

    def _session(self, session: object) -> AsyncSession:
        if not isinstance(session, AsyncSession):
            raise TypeError(f"expected AsyncSession, got {type(session).__name__}")
        return session

    async def latest_state(self, logical_document_id: str) -> StoredFingerprintState | None:
        async with self._session_maker() as session:
            stmt = (
                select(
                    Document.logical_document_id,
                    Document.id,
                    Document.version_number,
                    Document.fingerprint,
                    Document.is_active,
                )
                .where(Document.logical_document_id == logical_document_id)
                .order_by(Document.version_number.desc())
                .limit(1)
            )
            row = (await session.execute(stmt)).first()
            if row is None:
                return None
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
    ) -> PersistCandidateResult:
        typed = self._session(session)

        # Concurrency safety: check latest committed version within transaction with row lock
        is_postgres = _is_postgres_session(typed)
        select_latest = (
            select(Document)
            .where(Document.logical_document_id == logical_document_id)
            .order_by(Document.version_number.desc())
            .limit(1)
        )
        if is_postgres:
            select_latest = select_latest.with_for_update()
        latest_row = (await typed.execute(select_latest)).scalar_one_or_none()
        if (
            latest_row is not None
            and latest_row.fingerprint == fingerprint
            and latest_row.is_active
        ):
            return PersistCandidateResult(
                latest_row.id, latest_row.version_number, is_deduplicated=True
            )

        # If another worker committed a new version concurrently, calculate next version cleanly
        actual_version = (
            (latest_row.version_number + 1) if latest_row is not None else version_number
        )
        if actual_version != version_number:
            actual_document_id = f"doc-{hashlib.sha256(f'{logical_document_id}#v{actual_version}'.encode()).hexdigest()[:32]}"
        else:
            actual_document_id = document_id

        # Remap chunk IDs if actual_document_id changed to avoid DocumentChunk.id PK collisions
        parents_by_old_id = {p.id: p for p in parents}
        parent_id_map: dict[str, str] = {}
        for parent in parents:
            if actual_document_id != document_id:
                real_ids = parent.source_block_ids
                new_pid = parent_chunk_id(
                    document_version_id=actual_document_id,
                    heading_path=parent.heading_path,
                    block_range=(
                        real_ids[0] if real_ids else None,
                        real_ids[-1] if real_ids else None,
                    ),
                    ordinal=parent.ordinal,
                    version=parent.parent_chunker_version,
                )
            else:
                new_pid = parent.id
            parent_id_map[parent.id] = new_pid

        admin_meta = (
            parents[0].administrative_metadata
            if parents and parents[0].administrative_metadata
            else {}
        )

        document = Document(
            id=actual_document_id,
            user_id=None,
            logical_document_id=logical_document_id,
            version_number=actual_version,
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
                "administrative_metadata": admin_meta,
            },
        )
        typed.add(document)

        for parent in parents:
            new_pid = parent_id_map[parent.id]
            typed.add(
                DocumentChunk(
                    id=new_pid,
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
                    metadata_={
                        "ordinal": parent.ordinal,
                        "administrative_metadata": parent.administrative_metadata,
                    },
                )
            )
        for child, vector in children:
            if actual_document_id != document_id:
                new_pid = parent_id_map.get(child.parent_id, child.parent_id)
                parent_obj = parents_by_old_id.get(child.parent_id)
                if parent_obj is not None:
                    real_ids = parent_obj.source_block_ids
                else:
                    real_ids = child.source_block_ids
                new_cid = child_chunk_id(
                    parent_id=new_pid,
                    block_range=(
                        real_ids[0] if real_ids else None,
                        real_ids[-1] if real_ids else None,
                    ),
                    ordinal=child.chunk_index,
                    content_hash=child.content_hash,
                    version=child.child_chunker_version,
                )
            else:
                new_pid = child.parent_id
                new_cid = child.id

            typed.add(
                DocumentChunk(
                    id=new_cid,
                    document_id=document.id,
                    chunk_index=child.chunk_index,
                    hierarchy_level=1,
                    node_type=child.level.value,
                    parent_id=new_pid,
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
                    metadata_={
                        "administrative_metadata": child.administrative_metadata,
                    },
                )
            )
        await typed.flush()
        return PersistCandidateResult(document.id, actual_version, is_deduplicated=False)

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
