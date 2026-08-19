"""Unit tests for P3 telemetry retention."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.db.base import Base
from app.infrastructure.db.models import AssistantRun, AuditOutbox, ToolExecution, User
from app.services.retention import RETENTION_JOB_AUTHORITY, RetentionService


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Create an isolated SQLite database for retention behavior."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_expired_telemetry_preserves_pending_audit_outbox(
    db_session: AsyncSession,
) -> None:
    """Verify expiry deletes delivered telemetry but never a run with pending audit delivery."""
    with pytest.raises(PermissionError, match="scheduled retention authority"):
        await RetentionService.purge_expired(db_session)

    now = datetime.now(UTC)
    expired_at = now - timedelta(days=91)
    user = User(email="retention@example.com")
    db_session.add(user)
    await db_session.flush()

    expired_run = AssistantRun(
        id="expired_run",
        user_id=user.id,
        correlation_id="corr_expired",
        request="old request",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        status="completed",
        completed_at=expired_at,
    )
    protected_run = AssistantRun(
        id="protected_run",
        user_id=user.id,
        correlation_id="corr_protected",
        request="old pending audit",
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        status="completed",
        completed_at=expired_at,
    )
    db_session.add_all([expired_run, protected_run])
    await db_session.flush()

    db_session.add_all(
        [
            ToolExecution(
                run_id=expired_run.id,
                tool_name="test.tool",
                input_parameters={},
                success=True,
                latency_ms=1.0,
                executed_at=expired_at,
            ),
            AuditOutbox(
                run_id=expired_run.id,
                event_kind="tool_execution",
                payload={},
                status="delivered",
                delivered_at=expired_at,
            ),
            AuditOutbox(
                run_id=protected_run.id,
                event_kind="tool_execution",
                payload={},
                status="pending",
            ),
        ]
    )
    await db_session.flush()

    deleted = await RetentionService.purge_expired(
        db_session,
        now=now,
        authorized_by=RETENTION_JOB_AUTHORITY,
    )

    assert deleted["assistant_runs"] == 1
    assert (await db_session.execute(select(AssistantRun).where(AssistantRun.id == expired_run.id))).first() is None
    assert (
        await db_session.execute(select(AssistantRun).where(AssistantRun.id == protected_run.id))
    ).first() is not None
    assert (await db_session.execute(select(AuditOutbox).where(AuditOutbox.status == "pending"))).first()
