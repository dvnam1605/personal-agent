"""Unit tests for production audit and retention worker entry points."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.db.base import Base
from app.infrastructure.db.models import AssistantRun, AuditOutbox, ToolExecution, User
from app.services.audit import AuditOutboxWorker, AuditService
from app.services.retention import RetentionWorker


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create a database factory matching the production worker contract."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_audit_outbox_worker_delivers_and_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The production worker owns a transaction and materializes queued events."""
    async with session_factory() as session:
        user = User(email="worker@example.com")
        session.add(user)
        await session.flush()
        run = AssistantRun(
            id="run_worker",
            user_id=user.id,
            correlation_id="corr_worker",
            request="worker",
            route_type="direct_specialist",
            domains=["general"],
            complexity="direct",
            status="running",
        )
        session.add(run)
        await session.flush()
        await AuditService.record_tool_execution(
            session,
            run_id=run.id,
            tool_name="worker.tool",
            input_parameters={"value": 1},
            success=True,
            latency_ms=1.0,
        )
        await session.commit()

    worker = AuditOutboxWorker(session_factory, poll_interval_seconds=0.01, batch_size=10)
    assert await worker.run_once() == 1

    async with session_factory() as session:
        projection = await session.scalar(
            select(ToolExecution).where(ToolExecution.run_id == "run_worker")
        )
        outbox = await session.scalar(select(AuditOutbox).where(AuditOutbox.run_id == "run_worker"))
        assert projection is not None
        assert outbox is not None
        assert outbox.status == "delivered"


@pytest.mark.asyncio
async def test_audit_outbox_worker_marks_unsupported_rows_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Malformed event kinds are retryable failures rather than lost rows."""
    async with session_factory() as session:
        session.add(
            AuditOutbox(
                event_kind="unsupported",
                payload={},
                status="pending",
            )
        )
        await session.commit()

    worker = AuditOutboxWorker(session_factory, poll_interval_seconds=0.01)
    assert await worker.run_once() == 0
    async with session_factory() as session:
        outbox = await session.scalar(select(AuditOutbox))
        assert outbox is not None
        assert outbox.status == "failed"
        assert outbox.attempt_count == 1
        assert outbox.next_attempt_at is not None


@pytest.mark.asyncio
async def test_retention_worker_runs_authorized_purge(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The scheduled worker is the operational entry point for deletion."""
    async with session_factory() as session:
        user = User(email="retention-worker@example.com")
        session.add(user)
        await session.flush()
        session.add(
            AssistantRun(
                id="run_retention_worker",
                user_id=user.id,
                correlation_id="corr_retention_worker",
                request="old",
                route_type="direct_specialist",
                domains=["general"],
                complexity="direct",
                status="completed",
                completed_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        await session.commit()

    worker = RetentionWorker(session_factory, retention_days=1, interval_seconds=0.01)
    deleted = await worker.run_once()
    assert deleted["assistant_runs"] == 0
    assert deleted["messages"] == 0
