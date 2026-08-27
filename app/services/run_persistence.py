"""Service for persisting and synchronizing AssistantState and execution runs with full privacy guarantees."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.sanitization import sanitize_string
from app.domain.enums import RunStatus
from app.domain.models import AssistantState, BudgetUsage
from app.infrastructure.db.models import AssistantRun
from app.services.audit import AuditOutboxService, create_sanitized_state_snapshot, sanitize_payload

logger = structlog.get_logger(__name__)


def _bounded_text(value: str | None, max_len: int) -> str | None:
    """Sanitize and hard-truncate an internal bounded column value."""
    if value is None:
        return None
    return sanitize_string(value, max_len)[:max_len] or None


TERMINAL_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
}
VALID_TRANSITIONS = {
    RunStatus.PENDING.value: {RunStatus.RUNNING.value, RunStatus.CANCELLED.value},
    RunStatus.RUNNING.value: {
        RunStatus.WAITING_INPUT.value,
        RunStatus.WAITING_APPROVAL.value,
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    },
    RunStatus.WAITING_INPUT.value: {
        RunStatus.RUNNING.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    },
    RunStatus.WAITING_APPROVAL.value: {
        RunStatus.RUNNING.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    },
}


def _validate_transition(current_status: str, next_status: str) -> None:
    """Reject lifecycle changes that would make persisted checkpoint state ambiguous."""
    if current_status == next_status:
        return
    if next_status not in VALID_TRANSITIONS.get(current_status, set()):
        raise ValueError(f"Invalid run status transition: {current_status} -> {next_status}")


def _validate_expected_version(run: AssistantRun, expected_version: int | None) -> None:
    """Reject a caller operating on a checkpoint that was already superseded."""
    if expected_version is not None and run.state_version != expected_version:
        raise ValueError(
            f"Stale run version: expected {expected_version}, current {run.state_version}."
        )


class RunPersistenceService:
    """Manages the database lifecycle for assistant execution runs with privacy and telemetry guarantees."""

    @staticmethod
    async def create_run(
        session: AsyncSession,
        run_id: str,
        user_id: str,
        request: str,
        route_type: str,
        domains: list[str],
        complexity: str,
        correlation_id: str,
        langsmith_trace_id: str | None = None,
        langsmith_run_id: str | None = None,
        session_id: str | None = None,
        workflow_name: str | None = None,
        active_skill: str | None = None,
        goal: str | None = None,
    ) -> AssistantRun:
        """Create and persist a new running execution record with sanitized inputs."""
        clean_request = sanitize_payload(request, max_string_len=2000)
        clean_goal = sanitize_payload(goal, max_string_len=1000) if goal else None
        clean_domains = [sanitized for item in domains if (sanitized := _bounded_text(item, 64))]
        run = AssistantRun(
            id=run_id,
            session_id=session_id,
            user_id=user_id,
            correlation_id=_bounded_text(correlation_id, 64) or "unknown",
            langsmith_trace_id=langsmith_trace_id,
            langsmith_run_id=langsmith_run_id,
            request=clean_request,
            goal=clean_goal,
            route_type=_bounded_text(route_type, 32) or "unknown",
            domains=clean_domains,
            complexity=_bounded_text(complexity, 32) or "unknown",
            workflow_name=_bounded_text(workflow_name, 64),
            active_skill=_bounded_text(active_skill, 64),
            status=RunStatus.RUNNING.value,
            telemetry_degraded=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        return run

    @staticmethod
    async def get_run(session: AsyncSession, run_id: str) -> AssistantRun | None:
        """Retrieve an existing assistant run by ID."""
        stmt = select(AssistantRun).where(AssistantRun.id == run_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update_run_state(
        session: AsyncSession,
        run_id: str,
        state: AssistantState,
        expected_version: int | None = None,
    ) -> AssistantRun | None:
        """Synchronize in-flight state metrics and sanitized snapshot to database."""
        run = await RunPersistenceService.get_run(session, run_id)
        if run is None:
            logger.warning("run_not_found_for_update", run_id=run_id)
            return None

        _validate_expected_version(run, expected_version)
        next_status = state.status.value
        if next_status in TERMINAL_STATUSES:
            raise ValueError("Use complete_run() to persist a terminal run status.")
        _validate_transition(run.status, next_status)

        run.status = next_status
        run.iteration = state.iteration
        run.react_steps = state.react_steps
        run.tool_call_count = state.tool_call_count
        run.llm_call_count = state.llm_call_count
        run.state_snapshot = create_sanitized_state_snapshot(state)
        run.telemetry_degraded = await AuditOutboxService.has_pending(session, run_id)
        run.updated_at = datetime.now(UTC)

        try:
            await session.flush()
        except StaleDataError as exc:
            await session.rollback()
            raise ValueError("Stale run version conflict while persisting checkpoint.") from exc
        return run

    @staticmethod
    async def load_state(session: AsyncSession, run_id: str) -> AssistantState | None:
        """Rehydrate the full sanitized continuation state after a process restart."""
        run = await RunPersistenceService.get_run(session, run_id)
        if run is None:
            return None

        snapshot = dict(run.state_snapshot or {})
        # Older checkpoints may not contain the fields required to construct AssistantState.
        snapshot.setdefault("run_id", run.id)
        snapshot.setdefault("user_id", run.user_id)
        snapshot.setdefault("request", run.request)
        snapshot.setdefault("goal", run.goal)
        snapshot.setdefault("active_skill", run.active_skill)
        snapshot.setdefault("active_workflow", run.workflow_name)
        # The row status is authoritative if a worker crashed between state construction
        # and checkpoint serialization.
        snapshot["status"] = run.status
        snapshot.setdefault("iteration", run.iteration)
        snapshot.setdefault("react_steps", run.react_steps)
        snapshot.setdefault("tool_call_count", run.tool_call_count)
        snapshot.setdefault("llm_call_count", run.llm_call_count)
        try:
            return AssistantState.model_validate(snapshot)
        except ValueError as exc:
            logger.error("run_checkpoint_invalid", run_id=run_id, error=str(exc))
            raise ValueError(f"Persisted run checkpoint cannot be rehydrated: {run_id}") from exc

    @staticmethod
    async def resume_waiting_input(
        session: AsyncSession,
        run_id: str,
        response: Any,
        expected_version: int | None = None,
    ) -> AssistantState:
        """Attach sanitized user input to a waiting checkpoint and move it back to running."""
        run = await RunPersistenceService.get_run(session, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        if run.status != RunStatus.WAITING_INPUT.value:
            raise ValueError(f"Run {run_id} is not waiting for input.")
        state = await RunPersistenceService.load_state(session, run_id)
        if state is None:
            raise ValueError(f"Run checkpoint not found: {run_id}")
        continuation_context = dict(state.continuation_context)
        continuation_context["last_input"] = sanitize_payload(
            response,
            max_string_len=2_000,
            max_depth=8,
            max_items=100,
            max_payload_bytes=16_384,
        )
        resumed_state = state.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "continuation_context": continuation_context,
            }
        )
        await RunPersistenceService.update_run_state(
            session,
            run_id,
            resumed_state,
            expected_version=expected_version,
        )
        return resumed_state

    @staticmethod
    async def complete_run(
        session: AsyncSession,
        run_id: str,
        status: RunStatus | str,
        total_latency_ms: float,
        budget_usage: BudgetUsage | None = None,
        state: AssistantState | None = None,
        error_summary: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost_usd: Decimal = Decimal("0.000000"),
        parallel_time_ms: float = 0.0,
        expected_version: int | None = None,
    ) -> AssistantRun | None:
        """Finalize an execution run record with terminal status and diagnostic telemetry."""
        run = await RunPersistenceService.get_run(session, run_id)
        if run is None:
            logger.warning("run_not_found_for_completion", run_id=run_id)
            return None

        _validate_expected_version(run, expected_version)
        status_val = status.value if isinstance(status, RunStatus) else str(status)
        if status_val not in TERMINAL_STATUSES:
            raise ValueError(f"complete_run() requires a terminal status, got {status_val!r}.")
        if run.status in TERMINAL_STATUSES:
            if run.status == status_val:
                # Terminal completion is an idempotent retry. Never rewrite completed_at.
                return run
            raise ValueError(f"Invalid terminal run transition: {run.status} -> {status_val}")
        _validate_transition(run.status, status_val)
        run.status = status_val
        run.total_latency_ms = total_latency_ms
        run.completed_at = datetime.now(UTC)
        run.updated_at = datetime.now(UTC)
        run.error_summary = (
            sanitize_payload(error_summary, max_string_len=1000) if error_summary else None
        )
        run.prompt_tokens = prompt_tokens
        run.completion_tokens = completion_tokens
        run.total_tokens = total_tokens
        run.estimated_cost_usd = estimated_cost_usd
        run.parallel_time_ms = parallel_time_ms

        if budget_usage:
            run.tool_call_count = budget_usage.tool_calls
            run.llm_call_count = budget_usage.llm_calls
            run.react_steps = budget_usage.react_steps

        if state:
            snapshot_state = state.model_copy(update={"status": RunStatus(status_val)})
            run.state_snapshot = create_sanitized_state_snapshot(snapshot_state)

        run.telemetry_degraded = await AuditOutboxService.has_pending(session, run_id)

        try:
            await session.flush()
        except StaleDataError as exc:
            await session.rollback()
            raise ValueError("Stale run version conflict while completing run.") from exc
        return run
