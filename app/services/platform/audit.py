"""Privacy-preserving audit logging, bounded sanitization, and durable outbox delivery."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.sanitization import (
    mask_email,
    sanitize_payload,
    sanitize_string,
)
from app.domain.models import AssistantState
from app.infrastructure.db.models import AuditEvent, AuditOutbox, LLMExecution, ToolExecution

logger = structlog.get_logger(__name__)

__all__ = [
    "AuditOutboxService",
    "AuditOutboxWorker",
    "AuditService",
    "create_sanitized_state_snapshot",
    "mask_email",
    "sanitize_payload",
    "sanitize_string",
]

STATE_SNAPSHOT_ALLOWLIST = {
    "run_id",
    "user_id",
    "normalized_request",
    "route_decision",
    "goal",
    "entities",
    "active_skill",
    "active_workflow",
    "plan",
    "task_results",
    "evidence",
    "missing_information",
    "proposed_actions",
    "approvals",
    "continuation_context",
    "task_context",
    "iteration",
    "react_steps",
    "tool_call_count",
    "llm_call_count",
    "status",
    "errors",
}

UNRESOLVED_OUTBOX_STATUSES = ("pending", "processing", "failed")
DEFAULT_CLAIM_TIMEOUT_SECONDS = 300

_CONNECTION_CLOSED_MARKERS = (
    "connection was closed",
    "connection is closed",
    "connection does not exist",
)


def is_db_connection_closed_error(exc: BaseException) -> bool:
    """Detect a DB connection torn down mid-query (e.g. Postgres stopping on shutdown).

    Shutdown races surface as asyncpg ``ConnectionDoesNotExistError`` (sometimes
    wrapped in the driver's adapted ``Error``) rather than a SQLAlchemy
    ``DBAPIError`` with ``connection_invalidated`` set, so walk the whole cause
    chain. These are benign: the batch rolls back and rows stay queued for the
    next boot, so workers can log them quietly instead of as errors.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, DBAPIError) and current.connection_invalidated:
            return True
        module = type(current).__module__ or ""
        name = type(current).__name__
        if "asyncpg" in module and ("Connection" in name or "Interface" in name):
            return True
        if any(marker in str(current).lower() for marker in _CONNECTION_CLOSED_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def _sanitize_column(value: str | None, max_len: int) -> str | None:
    """Sanitize a bounded SQL string without allowing the truncation marker past its column."""
    if value is None:
        return None
    return sanitize_string(value, max_len)[:max_len]


def create_sanitized_state_snapshot(state: AssistantState) -> dict[str, Any]:
    """Create a bounded, allowlisted snapshot that can reconstruct a waiting run."""
    raw_dict = state.model_dump(mode="json")
    return sanitize_payload(
        raw_dict,
        max_string_len=1000,
        allowed_keys=STATE_SNAPSHOT_ALLOWLIST,
        max_depth=10,
        max_items=100,
        max_payload_bytes=64_000,
    )


def _serialize_outbox_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Convert nested non-JSON audit values into lossless JSON-compatible values."""
    return {str(key): _serialize_outbox_value(value) for key, value in data.items()}


def _serialize_outbox_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize_outbox_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_outbox_value(item) for item in value]
    return value


def _deserialize_outbox_payload(event_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Restore typed values needed by append-only audit projections."""
    restored = dict(payload)
    timestamp_field = "created_at" if event_kind == "audit_event" else "executed_at"
    if isinstance(restored.get(timestamp_field), str):
        restored[timestamp_field] = datetime.fromisoformat(restored[timestamp_field])
    if event_kind == "llm_execution" and isinstance(restored.get("estimated_cost_usd"), str):
        restored["estimated_cost_usd"] = Decimal(restored["estimated_cost_usd"])
    return restored


class AuditOutboxService:
    """Durably queue and materialize sanitized audit events with worker-safe claims."""

    @staticmethod
    async def enqueue(
        session: AsyncSession,
        event_kind: str,
        payload: dict[str, Any],
        run_id: str | None,
    ) -> AuditOutbox:
        """Persist a bounded audit event before it is projected into a reporting table."""
        clean_payload = sanitize_payload(
            payload,
            max_string_len=1000,
            max_depth=8,
            max_items=100,
            max_payload_bytes=32_768,
        )
        outbox_event = AuditOutbox(
            run_id=run_id,
            event_kind=_sanitize_column(event_kind, 32) or "unknown",
            payload=_serialize_outbox_payload(clean_payload),
            status="pending",
        )
        session.add(outbox_event)
        await session.flush()
        return outbox_event

    @staticmethod
    async def has_pending(session: AsyncSession, run_id: str) -> bool:
        """Return whether a run has audit events not yet materialized."""
        stmt = select(AuditOutbox.id).where(
            AuditOutbox.run_id == run_id,
            AuditOutbox.status.in_(UNRESOLVED_OUTBOX_STATUSES),
        )
        return (await session.execute(stmt)).first() is not None

    @staticmethod
    async def _recover_stale_claims(
        session: AsyncSession,
        now: datetime,
        claim_timeout_seconds: int,
    ) -> None:
        """Return abandoned processing rows to the retryable queue."""
        stale_before = now - timedelta(seconds=claim_timeout_seconds)
        await session.execute(
            update(AuditOutbox)
            .where(
                AuditOutbox.status == "processing",
                AuditOutbox.claimed_at.is_not(None),
                AuditOutbox.claimed_at < stale_before,
            )
            .values(
                status="pending",
                claimed_by=None,
                claimed_at=None,
                next_attempt_at=now,
            )
        )

    @staticmethod
    async def _claim_entries(
        session: AsyncSession,
        run_id: str | None,
        limit: int,
        worker_id: str,
        now: datetime,
        claim_timeout_seconds: int,
    ) -> list[AuditOutbox]:
        if limit <= 0:
            return []
        await AuditOutboxService._recover_stale_claims(
            session, now=now, claim_timeout_seconds=claim_timeout_seconds
        )
        retryable = or_(
            AuditOutbox.status == "pending",
            (AuditOutbox.status == "failed")
            & (AuditOutbox.next_attempt_at.is_(None) | (AuditOutbox.next_attempt_at <= now)),
        )
        stmt = (
            select(AuditOutbox)
            .where(retryable)
            .order_by(AuditOutbox.created_at, AuditOutbox.id)
            .with_for_update(skip_locked=True)
        )
        if run_id is not None:
            stmt = stmt.where(AuditOutbox.run_id == run_id)
        stmt = stmt.limit(limit)
        entries = list((await session.execute(stmt)).scalars())
        for entry in entries:
            entry.status = "processing"
            entry.claimed_by = worker_id
            entry.claimed_at = now
        await session.flush()
        return entries

    @staticmethod
    def _projection_model(
        event_kind: str,
    ) -> type[ToolExecution] | type[LLMExecution] | type[AuditEvent]:
        if event_kind == "tool_execution":
            return ToolExecution
        if event_kind == "llm_execution":
            return LLMExecution
        if event_kind == "audit_event":
            return AuditEvent
        raise ValueError(f"Unsupported audit outbox event kind: {event_kind}")

    @staticmethod
    async def _projection_exists(session: AsyncSession, entry: AuditOutbox) -> bool:
        model = AuditOutboxService._projection_model(entry.event_kind)
        stmt = select(model.id).where(model.outbox_id == entry.id)
        return (await session.execute(stmt)).first() is not None

    @staticmethod
    async def _project_entry(session: AsyncSession, entry: AuditOutbox) -> None:
        if await AuditOutboxService._projection_exists(session, entry):
            return
        payload = _deserialize_outbox_payload(entry.event_kind, entry.payload)
        payload["outbox_id"] = entry.id
        model = AuditOutboxService._projection_model(entry.event_kind)
        session.add(model(**payload))
        await session.flush()

    @staticmethod
    async def deliver_pending(
        session: AsyncSession,
        run_id: str | None = None,
        limit: int = 100,
        worker_id: str | None = None,
        claim_timeout_seconds: int = DEFAULT_CLAIM_TIMEOUT_SECONDS,
        now: datetime | None = None,
    ) -> int:
        """Claim and materialize retryable rows; callers commit the surrounding transaction."""
        if claim_timeout_seconds <= 0:
            raise ValueError("claim_timeout_seconds must be positive")
        current_time = now or datetime.now(UTC)
        entries = await AuditOutboxService._claim_entries(
            session,
            run_id=run_id,
            limit=limit,
            worker_id=worker_id or str(uuid.uuid4()),
            now=current_time,
            claim_timeout_seconds=claim_timeout_seconds,
        )
        delivered = 0

        for entry in entries:
            entry.attempt_count += 1
            try:
                async with session.begin_nested():
                    await AuditOutboxService._project_entry(session, entry)
                entry.status = "delivered"
                entry.delivered_at = current_time
                entry.last_error = None
                entry.next_attempt_at = None
                entry.claimed_by = None
                entry.claimed_at = None
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - per-entry delivery isolation
                # A concurrent/previous projection is a successful idempotent delivery.
                try:
                    already_projected = await AuditOutboxService._projection_exists(session, entry)
                except Exception:  # noqa: BLE001 - existence check must not mask delivery
                    logger.exception("audit_projection_exists_check_failed")
                    already_projected = False
                if already_projected:
                    entry.status = "delivered"
                    entry.delivered_at = current_time
                    entry.last_error = None
                    entry.next_attempt_at = None
                    entry.claimed_by = None
                    entry.claimed_at = None
                    delivered += 1
                    continue

                entry.status = "failed"
                entry.last_error = sanitize_string(str(exc), max_string_len=500)
                entry.next_attempt_at = current_time + timedelta(
                    seconds=min(300, 2 ** min(entry.attempt_count, 8))
                )
                entry.claimed_by = None
                entry.claimed_at = None
                logger.error(
                    "audit_outbox_delivery_failed",
                    outbox_id=entry.id,
                    event_kind=entry.event_kind,
                    attempt_count=entry.attempt_count,
                    error=entry.last_error,
                )

        await session.flush()
        return delivered


class AuditOutboxWorker:
    """Long-running production delivery loop backed by database transactions."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        poll_interval_seconds: float = 1.0,
        batch_size: int = 100,
    ) -> None:
        if poll_interval_seconds <= 0 or batch_size <= 0:
            raise ValueError("poll_interval_seconds and batch_size must be positive")
        self.session_factory = session_factory
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.worker_id = str(uuid.uuid4())

    async def run_once(self) -> int:
        """Deliver one batch and commit the claim/projection atomically."""
        async with self.session_factory() as session:
            try:
                delivered = await AuditOutboxService.deliver_pending(
                    session,
                    limit=self.batch_size,
                    worker_id=self.worker_id,
                )
                await session.commit()
                return delivered
            except Exception as exc:  # noqa: BLE001 - worker iteration isolation
                await session.rollback()
                if is_db_connection_closed_error(exc):
                    logger.warning("audit_outbox_worker_db_closed")
                else:
                    logger.exception("audit_outbox_worker_iteration_failed")
                return 0

    async def run(self, stop_event: asyncio.Event) -> None:
        """Poll until the application asks the worker to stop."""
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                continue


class AuditService:
    """Service to durably queue sanitized tool, LLM, and security audit events."""

    @staticmethod
    async def record_tool_execution(
        session: AsyncSession,
        run_id: str,
        tool_name: str,
        input_parameters: dict[str, Any],
        success: bool,
        latency_ms: float,
        agent_name: str | None = None,
        output_summary: dict[str, Any] | None = None,
        error_message: str | None = None,
        cached: bool = False,
        retry_count: int = 0,
    ) -> AuditOutbox:
        """Queue a bounded tool execution for durable, retryable materialization."""
        record_data = {
            "run_id": run_id,
            "agent_name": _sanitize_column(agent_name, 64),
            "tool_name": _sanitize_column(tool_name, 64) or "unknown",
            "input_parameters": sanitize_payload(input_parameters),
            "output_summary": sanitize_payload(output_summary)
            if output_summary is not None
            else None,
            "success": success,
            "error_message": sanitize_payload(error_message) if error_message is not None else None,
            "latency_ms": latency_ms,
            "cached": cached,
            "retry_count": retry_count,
            "executed_at": datetime.now(UTC),
        }
        return await AuditOutboxService.enqueue(
            session, event_kind="tool_execution", payload=record_data, run_id=run_id
        )

    @staticmethod
    async def record_llm_execution(
        session: AsyncSession,
        run_id: str,
        provider: str,
        model: str,
        purpose: str,
        latency_ms: float,
        success: bool,
        agent_name: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost_usd: Decimal = Decimal("0.000000"),
        error_message: str | None = None,
    ) -> AuditOutbox:
        """Queue an LLM audit record without prompts or hidden reasoning."""
        record_data = {
            "run_id": run_id,
            "agent_name": _sanitize_column(agent_name, 64),
            "provider": _sanitize_column(provider, 32) or "unknown",
            "model": _sanitize_column(model, 64) or "unknown",
            "purpose": _sanitize_column(purpose, 64) or "unknown",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": estimated_cost_usd,
            "latency_ms": latency_ms,
            "success": success,
            "error_message": sanitize_payload(error_message) if error_message is not None else None,
            "executed_at": datetime.now(UTC),
        }
        return await AuditOutboxService.enqueue(
            session, event_kind="llm_execution", payload=record_data, run_id=run_id
        )

    @staticmethod
    async def record_audit_event(
        session: AsyncSession,
        event_type: str,
        component: str,
        severity: str,
        payload: dict[str, Any],
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> AuditOutbox:
        """Queue a bounded security or policy audit event."""
        record_data = {
            "run_id": run_id,
            "user_id": user_id,
            "event_type": _sanitize_column(event_type, 64) or "unknown",
            "component": _sanitize_column(component, 64) or "unknown",
            "severity": _sanitize_column(severity, 16) or "INFO",
            "payload": sanitize_payload(payload),
            "created_at": datetime.now(UTC),
        }
        return await AuditOutboxService.enqueue(
            session, event_kind="audit_event", payload=record_data, run_id=run_id
        )
