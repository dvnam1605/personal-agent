"""Unit tests for production audit and retention worker entry points."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.db.base import Base
from app.infrastructure.db.models import AssistantRun, AuditOutbox, ToolExecution, User
from app.services.platform.audit import (
    AuditOutboxService,
    AuditOutboxWorker,
    AuditService,
    is_db_connection_closed_error,
)
from app.services.platform.retention import RetentionService, RetentionWorker


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


def _invalidated_disconnect() -> StatementError:
    """Build a pool-invalidated disconnect like a Postgres torn down on shutdown."""
    return DBAPIError.instance(
        "SELECT 1",
        {},
        Exception("connection was closed"),
        Exception,
        connection_invalidated=True,
    )


def test_is_db_connection_closed_error_detects_invalidated_disconnect() -> None:
    """Pool-invalidated disconnects are shutdown noise, not delivery bugs."""
    assert is_db_connection_closed_error(_invalidated_disconnect())


def test_is_db_connection_closed_error_ignores_genuine_errors() -> None:
    """Real query errors must keep the loud error-level log path."""
    assert not is_db_connection_closed_error(ValueError("constraint violated"))
    plain = DBAPIError.instance("SELECT 1", {}, Exception("boom"), Exception)
    assert not is_db_connection_closed_error(plain)


def test_is_db_connection_closed_error_detects_wrapped_asyncpg_shutdown() -> None:
    """Match the observed shutdown chain: adapted Error caused by asyncpg close."""
    connection_error = type(
        "ConnectionDoesNotExistError",
        (Exception,),
        {"__module__": "asyncpg.exceptions"},
    )("connection was closed in the middle of operation")
    adapted = type(
        "Error",
        (Exception,),
        {"__module__": "sqlalchemy.dialects.postgresql.asyncpg"},
    )(
        "<class 'asyncpg.exceptions.ConnectionDoesNotExistError'>: "
        "connection was closed in the middle of operation"
    )
    adapted.__cause__ = connection_error
    assert is_db_connection_closed_error(adapted)


@pytest.mark.asyncio
async def test_audit_outbox_worker_returns_zero_on_db_shutdown(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB torn down mid-iteration rolls back quietly instead of raising."""

    async def _raise_closed(*args: object, **kwargs: object) -> int:
        raise _invalidated_disconnect()

    monkeypatch.setattr(AuditOutboxService, "deliver_pending", _raise_closed)
    worker = AuditOutboxWorker(session_factory, poll_interval_seconds=0.01)
    assert await worker.run_once() == 0


@pytest.mark.asyncio
async def test_retention_worker_returns_empty_on_db_shutdown(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retention worker shares the same quiet shutdown-race contract."""

    async def _raise_closed(*args: object, **kwargs: object) -> dict[str, int]:
        raise _invalidated_disconnect()

    monkeypatch.setattr(RetentionService, "purge_expired", _raise_closed)
    worker = RetentionWorker(session_factory, retention_days=1, interval_seconds=0.01)
    assert await worker.run_once() == {}
