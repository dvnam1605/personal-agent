"""Retention service and scheduled operational purge worker for P3 telemetry."""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models import (
    ApprovalRequest,
    AssistantRun,
    AuditOutbox,
    Message,
    WorkflowRun,
)
from app.services.approvals import ApprovalRequestService

logger = structlog.get_logger(__name__)

# Opaque capability passed only by the scheduled worker; callers cannot authorize deletion by
# supplying a descriptive string that merely looks like a role name.
RETENTION_JOB_AUTHORITY = object()
UNRESOLVED_OUTBOX_STATUSES = ("pending", "processing", "failed")


class RetentionService:
    """Purge expired telemetry only through an explicit operational authority boundary."""

    @staticmethod
    async def purge_expired(
        session: AsyncSession,
        retention_days: int = 90,
        now: datetime | None = None,
        authorized_by: object | None = None,
    ) -> dict[str, int]:
        """Delete eligible telemetry while preserving runs with any unresolved outbox row."""
        if authorized_by != RETENTION_JOB_AUTHORITY:
            raise PermissionError("Retention purge requires the scheduled retention authority.")
        if retention_days <= 0:
            raise ValueError("retention_days must be positive.")
        cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)

        unresolved_for_run = (
            select(AuditOutbox.id)
            .where(
                AuditOutbox.run_id == AssistantRun.id,
                AuditOutbox.status.in_(UNRESOLVED_OUTBOX_STATUSES),
            )
            .exists()
        )
        expired_run_ids = list(
            (
                await session.execute(
                    select(AssistantRun.id).where(
                        AssistantRun.completed_at.is_not(None),
                        AssistantRun.completed_at < cutoff,
                        ~unresolved_for_run,
                    )
                )
            ).scalars()
        )

        deleted: dict[str, int] = {}
        deleted["audit_outbox"] = _row_count(
            await session.execute(
                delete(AuditOutbox).where(
                    AuditOutbox.status == "delivered",
                    AuditOutbox.delivered_at.is_not(None),
                    AuditOutbox.delivered_at < cutoff,
                )
            )
        )
        # Orphaned unresolved rows have no run lifecycle to protect them; bound their lifetime.
        deleted["orphan_audit_outbox"] = _row_count(
            await session.execute(
                delete(AuditOutbox).where(
                    AuditOutbox.run_id.is_(None),
                    AuditOutbox.status.in_(UNRESOLVED_OUTBOX_STATUSES),
                    AuditOutbox.created_at < cutoff,
                )
            )
        )
        # tool_executions, llm_executions, and audit_events are append-only (M7).
        deleted["tool_executions"] = 0
        deleted["llm_executions"] = 0
        deleted["audit_events"] = 0

        if expired_run_ids:
            deleted["messages"] = _row_count(
                await session.execute(delete(Message).where(Message.run_id.in_(expired_run_ids)))
            )
            deleted["workflow_runs"] = _row_count(
                await session.execute(
                    delete(WorkflowRun).where(WorkflowRun.run_id.in_(expired_run_ids))
                )
            )
            deleted["approval_requests"] = _row_count(
                await session.execute(
                    delete(ApprovalRequest).where(ApprovalRequest.run_id.in_(expired_run_ids))
                )
            )
            # Keep assistant_runs: they parent append-only tool/llm/audit rows (M7).
            deleted["assistant_runs"] = 0
        else:
            deleted.update(messages=0, workflow_runs=0, approval_requests=0, assistant_runs=0)

        await session.flush()
        return deleted


class RetentionWorker:
    """Daily production purge loop with an explicit deletion authority."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        retention_days: int = 90,
        interval_seconds: float = 86_400.0,
    ) -> None:
        if retention_days <= 0 or interval_seconds <= 0:
            raise ValueError("retention_days and interval_seconds must be positive")
        self.session_factory = session_factory
        self.retention_days = retention_days
        self.interval_seconds = interval_seconds

    async def run_once(self) -> dict[str, int]:
        """Run and commit one authorized retention purge."""
        async with self.session_factory() as session:
            try:
                expired_approvals = await ApprovalRequestService.expire_pending(session)
                deleted = await RetentionService.purge_expired(
                    session,
                    retention_days=self.retention_days,
                    authorized_by=RETENTION_JOB_AUTHORITY,
                )
                deleted["expired_approval_requests"] = expired_approvals
                await session.commit()
                return deleted
            except Exception:  # noqa: BLE001 - worker iteration isolation
                await session.rollback()
                logger.exception("retention_worker_iteration_failed")
                return {}

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run once at startup and then at the configured maintenance interval."""
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue


def _row_count(result: object) -> int:
    """Extract a DBAPI row count without assuming a particular SQLAlchemy result type."""
    value = getattr(result, "rowcount", 0)
    return value if isinstance(value, int) else 0
