"""Persistence ports for the ingestion pipeline (spec P9D-2).

The orchestrator depends on these narrow protocols; the SQLAlchemy adapter
lives in ``app.infrastructure.db.ingestion_repository``. Reads manage their
own sessions; writes run inside a caller-owned transaction so candidate
persist + activation commit atomically.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol, runtime_checkable

from app.domain.models.ingestion import StoredFingerprintState
from app.domain.models.ingestion.chunks import ChildChunkDraft, ParentChunkDraft
from app.domain.models.ingestion.documents import SourceDocument


@runtime_checkable
class IngestionRepository(Protocol):
    async def latest_state(self, logical_document_id: str) -> StoredFingerprintState | None:
        """Latest known version state for a logical document (own session)."""
        ...

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
        children: list[tuple[ChildChunkDraft, list[float] | None]],
    ) -> tuple[str, int]:
        """Insert candidate version + chunks; returns (document_id, version)."""
        ...

    async def activate_candidate(
        self,
        session: object,
        *,
        logical_document_id: str,
        document_id: str,
    ) -> None:
        """Atomically mark the candidate ACTIVE and archive previous versions."""
        ...

    async def deactivate_logical_document(self, session: object, logical_document_id: str) -> bool:
        """Deleted-source policy: archive versions; True when rows changed."""
        ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Async context manager factory yielding a transactional session."""

    def transaction(self) -> AbstractAsyncContextManager[object]: ...
