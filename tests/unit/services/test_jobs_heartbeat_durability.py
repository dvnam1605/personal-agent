"""Unit tests for IngestionJobService heartbeat, durability, and fallback insert."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.models.ingestion import IngestionObservability, IngestionStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import IngestionJobRecord
from app.services.ingestion.jobs import SqlAlchemyIngestionJobStore


@pytest.fixture
async def session_maker():
    """Create in-memory SQLite async engine and session factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_job_heartbeat_updates_claimed_at(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that calling heartbeat updates the claimed_at timestamp for a running job."""
    service = SqlAlchemyIngestionJobStore(session_maker=session_maker)
    job_id = "job-heartbeat-1"

    # Pre-insert a RUNNING job with older claimed_at
    old_time = datetime.now(UTC) - timedelta(minutes=5)
    async with session_maker() as session:
        record = IngestionJobRecord(
            id=job_id,
            source_id="src-1",
            status="RUNNING",
            claimed_by="worker-1",
            claimed_at=old_time,
            payload={},
        )
        session.add(record)
        await session.commit()

    # Call heartbeat
    await service.heartbeat(job_id)

    # Verify claimed_at was refreshed
    async with session_maker() as session:
        updated = (
            await session.execute(select(IngestionJobRecord).where(IngestionJobRecord.id == job_id))
        ).scalar_one()
        assert updated.claimed_at is not None
        claimed_time = (
            updated.claimed_at.replace(tzinfo=UTC)
            if updated.claimed_at.tzinfo is None
            else updated.claimed_at
        )
        assert claimed_time > old_time


@pytest.mark.asyncio
async def test_claim_pending_reclaims_stale_jobs(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that claim_pending recovers stale running jobs older than timeout."""
    service = SqlAlchemyIngestionJobStore(session_maker=session_maker)
    job_id = "job-stale-1"

    # Pre-insert a RUNNING job with ancient claimed_at (e.g. 2000s ago)
    stale_time = datetime.now(UTC) - timedelta(seconds=2000)
    async with session_maker() as session:
        record = IngestionJobRecord(
            id=job_id,
            source_id="src-stale",
            status="RUNNING",
            claimed_by="dead-worker",
            claimed_at=stale_time,
            payload={},
        )
        session.add(record)
        await session.commit()

    # New worker claims pending jobs with 900s timeout
    claimed = await service.claim_pending(
        worker_id="new-worker",
        limit=5,
        claim_timeout_seconds=900,
    )

    assert len(claimed) == 1
    assert claimed[0].id == job_id
    assert claimed[0].claimed_by == "new-worker"
    assert claimed[0].status == "RUNNING"


@pytest.mark.asyncio
async def test_record_observability_inserts_fallback_when_row_missing(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that recording observability when the row is missing creates a fallback record."""
    service = SqlAlchemyIngestionJobStore(session_maker=session_maker)
    missing_job_id = "job-missing-1"

    obs = IngestionObservability(
        job_id=missing_job_id,
        source_id="src-orphan",
        status=IngestionStatus.COMPLETED,
        parent_count=2,
        child_count=5,
        table_child_count=0,
    )

    # Calling record on non-existent job row
    await service.record(obs)

    # Fallback row should have been created in database
    async with session_maker() as session:
        created = (
            await session.execute(
                select(IngestionJobRecord).where(IngestionJobRecord.id == missing_job_id)
            )
        ).scalar_one_or_none()
        assert created is not None
        assert created.id == missing_job_id
        assert created.status == "COMPLETED"
        assert created.parent_count == 2
        assert created.child_count == 5
