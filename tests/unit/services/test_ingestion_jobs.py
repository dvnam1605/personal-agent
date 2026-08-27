"""Unit tests for background batch execution (spec P9D-3)."""

import logging
from pathlib import Path
from typing import cast

from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from test_ingestion_orchestrator import make_orchestrator, make_source

from app.domain.models.ingestion import IngestionObservability, IngestionStatus
from app.infrastructure.db.models import IngestionJobRecord
from app.services.ingestion.jobs import SqlAlchemyIngestionJobStore, run_batch


async def _jobs_table_engine() -> AsyncEngine:
    """In-memory sqlite bound to ONLY the ingestion_jobs table (no pgvector)."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    jobs_table = cast(Table, IngestionJobRecord.__table__)

    def _create(sync_conn) -> None:
        jobs_table.create(sync_conn)

    async with engine.begin() as conn:
        await conn.run_sync(_create)
    return engine


MD_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "parsing"
    / "markdown"
    / "preparsed_sample.md"
)


class RecordingStore:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.recorded: list[IngestionStatus] = []

    async def create(self, job_id: str, source_id: str, payload: dict) -> None:
        del source_id, payload
        self.created.append(job_id)

    async def record(self, observability: IngestionObservability) -> None:
        self.recorded.append(observability.status)


async def test_batch_isolates_failures_per_file() -> None:
    orchestrator, repository = make_orchestrator()
    store = RecordingStore()
    good_md = MD_FIXTURE.read_bytes()

    sources = [
        (make_source(source_id="good-1"), good_md),
        (
            make_source(source_id="bad-1", source_type="upload", filename="blob.bin"),
            b"\x00\x01\x02\xffnot-a-document",
        ),
        (make_source(source_id="good-2"), good_md),
    ]
    results = await run_batch(orchestrator, sources, store=store, worker_id="w1")

    statuses = [obs.status for obs in results]
    assert statuses.count(IngestionStatus.COMPLETED) == 2
    assert statuses.count(IngestionStatus.FAILED) == 1
    assert len(store.created) == 3
    assert len(store.recorded) == 3
    # both good files ingested as separate documents despite the middle failure
    completed_ids = {obs.document_id for obs in results if obs.document_id}
    assert len(completed_ids) == 2
    assert len(repository.documents) == 2


def _completed_obs(job_id: str) -> IngestionObservability:
    return IngestionObservability(
        job_id=job_id,
        source_id="src-1",
        status=IngestionStatus.COMPLETED,
        parent_count=1,
        child_count=2,
    )


async def test_record_warns_when_job_row_missing(caplog) -> None:
    engine = await _jobs_table_engine()
    store = SqlAlchemyIngestionJobStore(async_sessionmaker(engine, expire_on_commit=False))

    with caplog.at_level(logging.WARNING, logger="app.services.ingestion.jobs"):
        await store.record(_completed_obs("missing-job"))

    assert any(
        record.getMessage() == "ingestion_job_record_row_missing" for record in caplog.records
    )
    await engine.dispose()


async def test_record_existing_row_emits_no_warning(caplog) -> None:
    engine = await _jobs_table_engine()
    store = SqlAlchemyIngestionJobStore(async_sessionmaker(engine, expire_on_commit=False))
    await store.create("real-job", "src-1", {"logical_document_id": None})

    with caplog.at_level(logging.WARNING, logger="app.services.ingestion.jobs"):
        await store.record(_completed_obs("real-job"))

    assert not caplog.records
    async with async_sessionmaker(engine)() as session:
        job = await session.get(IngestionJobRecord, "real-job")
        assert job is not None and job.status == "COMPLETED"
    await engine.dispose()
