"""Background ingestion jobs: durable store + batch runner (spec P9D-3).

The Postgres store follows the P3 audit-outbox claim pattern (claimed_by /
claimed_at with stale-claim recovery). ``run_batch`` executes sources through
the orchestrator one at a time — a failed file records FAILED and the batch
continues.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import select, update

from app.domain.models.documents import SourceDocument
from app.domain.models.ingestion import IngestionObservability, IngestionStatus
from app.infrastructure.db.models import IngestionJobRecord
from app.services.ingestion.orchestrator import logical_document_id_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

DEFAULT_CLAIM_TIMEOUT_SECONDS = 900


class IngestionJobStore(Protocol):
    async def create(self, job_id: str, source_id: str, payload: dict[str, object]) -> None: ...
    async def record(self, observability: IngestionObservability) -> None: ...
    async def heartbeat(self, job_id: str) -> None: ...


class SqlAlchemyIngestionJobStore:
    """Postgres implementation of the job store."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker

    async def create(self, job_id: str, source_id: str, payload: dict[str, object]) -> None:
        logical = payload.get("logical_document_id")
        async with self._session_maker() as session:
            session.add(
                IngestionJobRecord(
                    id=job_id,
                    source_id=source_id[:512],
                    logical_document_id=str(logical) if logical else None,
                    status="QUEUED",
                    payload=payload,
                )
            )
            await session.commit()

    async def record(self, observability: IngestionObservability) -> None:
        async with self._session_maker() as session:
            # L5: attempt_count counts EXECUTION attempts, so it only advances
            # on FAILED outcomes (successful/skipped runs are not retries).
            increment = observability.status is IngestionStatus.FAILED
            values: dict[str, object] = {
                "status": observability.status.value,
                "timings": {
                    "parse": observability.parse_duration_seconds,
                    "parents": observability.parent_build_duration_seconds,
                    "children": observability.child_build_duration_seconds,
                    "embedding": observability.embedding_duration_seconds,
                    "persist": observability.persist_duration_seconds,
                },
                "warnings": list(observability.warnings),
                "parent_count": observability.parent_count,
                "child_count": observability.child_count,
                "table_child_count": observability.table_child_count,
                "failure_reason": observability.failure_reason,
            }
            if increment:
                values["attempt_count"] = IngestionJobRecord.attempt_count + 1
            result = await session.execute(
                update(IngestionJobRecord)
                .where(IngestionJobRecord.id == observability.job_id)
                .values(**values)
            )
            await session.commit()
            if getattr(result, "rowcount", 0) == 0:
                logger.warning(
                    "ingestion_job_record_row_missing",
                    extra={"job_id": observability.job_id},
                )
                fallback = IngestionJobRecord(
                    id=observability.job_id,
                    source_id=observability.source_id,
                    status=observability.status.value,
                    attempt_count=1,
                    failure_reason=observability.failure_reason,
                    timings=values.get("timings"),
                    warnings=values.get("warnings"),
                    parent_count=observability.parent_count,
                    child_count=observability.child_count,
                    table_child_count=observability.table_child_count,
                )
                session.add(fallback)
                await session.commit()

    async def heartbeat(self, job_id: str) -> None:
        """Touch claimed_at timestamp so long-running jobs are not recovered as stale."""
        now = datetime.now(UTC)
        async with self._session_maker() as session:
            await session.execute(
                update(IngestionJobRecord)
                .where(
                    IngestionJobRecord.id == job_id,
                    IngestionJobRecord.status == "RUNNING",
                )
                .values(claimed_at=now)
            )
            await session.commit()

    async def claim_pending(
        self,
        worker_id: str,
        *,
        limit: int = 8,
        claim_timeout_seconds: int = DEFAULT_CLAIM_TIMEOUT_SECONDS,
    ) -> list[IngestionJobRecord]:
        """Claim QUEUED jobs; recover stale RUNNING claims first.

        Uses ``FOR UPDATE SKIP LOCKED`` on PostgreSQL so concurrent workers
        never double-claim (review M5); other dialects fall back to the plain
        select and remain single-worker-only.

        Stale recovery is time-based, mitigated by claim heartbeats: the
        orchestrator fires ``heartbeat`` on every stage transition and on
        every embedding batch (wired via ``run_batch``), so only a worker
        that stops heartbeating for longer than ``claim_timeout_seconds``
        is re-claimed. V1 still runs a single worker; scale-out must keep
        the heartbeat interval well below this bound.
        """
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=claim_timeout_seconds)
        claimed: list[IngestionJobRecord] = []
        async with self._session_maker() as session:
            await session.execute(
                update(IngestionJobRecord)
                .where(
                    IngestionJobRecord.status == "RUNNING",
                    IngestionJobRecord.claimed_at.is_not(None),
                    IngestionJobRecord.claimed_at < stale_before,
                )
                .values(status="QUEUED", claimed_by=None, claimed_at=None)
            )
            bind = session.get_bind() if hasattr(session, "get_bind") else session.bind
            is_postgres = bind is not None and getattr(bind.dialect, "name", "") == "postgresql"
            base_select = (
                select(IngestionJobRecord)
                .where(IngestionJobRecord.status == "QUEUED")
                .order_by(IngestionJobRecord.created_at)
                .limit(limit)
            )
            if is_postgres:
                base_select = base_select.with_for_update(skip_locked=True)
            rows = (await session.execute(base_select)).scalars().all()
            for row in rows:
                row.status = "RUNNING"
                row.claimed_by = worker_id
                row.claimed_at = now
                claimed.append(row)
            await session.commit()
        return claimed


class _NullJobStore:
    """No-op store used when callers don't need durability."""

    async def create(self, job_id: str, source_id: str, payload: dict[str, object]) -> None:
        del job_id, source_id, payload

    async def record(self, observability: IngestionObservability) -> None:
        del observability

    async def heartbeat(self, job_id: str) -> None:
        del job_id


async def run_batch(
    orchestrator,  # noqa: ANN001 - IngestionOrchestrator (avoids import cycle)
    sources: Sequence[tuple[SourceDocument, bytes]],
    *,
    store: IngestionJobStore | None = None,
    worker_id: str | None = None,
) -> list[IngestionObservability]:
    """Ingest every source independently; failures never abort the batch."""
    job_store = store or _NullJobStore()
    worker = worker_id or f"batch-{uuid.uuid4().hex[:8]}"
    results: list[IngestionObservability] = []
    for source, content in sources:
        job_id = uuid.uuid4().hex
        payload: dict[str, object] = {
            "worker_id": worker,
            "logical_document_id": logical_document_id_for(source.source_id),
        }
        try:
            await job_store.create(job_id, source.source_id, payload)
        except Exception:  # noqa: BLE001 - durability is best-effort, ingestion proceeds
            logger.exception("ingestion_job_create_failed", extra={"job_id": job_id})

        orig_heartbeat = getattr(orchestrator, "_heartbeat", None)
        if orig_heartbeat is None and hasattr(job_store, "heartbeat"):
            orchestrator._heartbeat = job_store.heartbeat
        try:
            observability = await orchestrator.ingest_source(source, content, job_id=job_id)
        finally:
            if orig_heartbeat is None and hasattr(orchestrator, "_heartbeat"):
                orchestrator._heartbeat = None

        results.append(observability)
        try:
            await job_store.record(observability)
        except Exception:  # noqa: BLE001 - recording must not fail the batch
            logger.exception("ingestion_job_record_failed", extra={"job_id": job_id})
        logger.info(
            "ingestion_job_finished",
            extra={
                "job_id": observability.job_id,
                "source_id": observability.source_id,
                "status": observability.status.value,
                "failure_reason": observability.failure_reason,
            },
        )
    return results


# Backward compatibility alias
IngestionJobService = SqlAlchemyIngestionJobStore
