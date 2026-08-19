"""Durable human-approval lifecycle and run continuation service."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sanitization import sanitize_payload, sanitize_string
from app.domain.enums import RunStatus
from app.domain.models import ActionApproval, AssistantState, ProposedAction
from app.infrastructure.db.models import ApprovalRequest
from app.services.run_persistence import RunPersistenceService

APPROVAL_STATUSES = {"pending", "approved", "rejected", "expired", "cancelled"}
APPROVAL_TRANSITIONS = {
    "pending": {"approved", "rejected", "expired", "cancelled"},
    "approved": set(),
    "rejected": set(),
    "expired": set(),
    "cancelled": set(),
}


def _utc(value: datetime | None) -> datetime | None:
    """Normalize database timestamps from backends that drop timezone metadata."""
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_approval_transition(current_status: str, next_status: str) -> None:
    """Enforce the approval state machine instead of allowing arbitrary ORM updates."""
    if current_status == next_status:
        return
    if next_status not in APPROVAL_TRANSITIONS.get(current_status, set()):
        raise ValueError(f"Invalid approval transition: {current_status} -> {next_status}")


def _proposal_fields(action: ProposedAction | ApprovalRequest) -> dict[str, Any]:
    if isinstance(action, ProposedAction):
        return {
            "action_type": action.action_type,
            "description": action.description,
            "tool_name": action.tool_name,
            "parameters": sanitize_payload(action.parameters),
            "risk_level": action.risk_level.value,
        }
    return {
        "action_type": action.action_type,
        "description": action.description,
        "tool_name": action.tool_name,
        "parameters": sanitize_payload(action.parameters),
        "risk_level": action.risk_level,
    }


def _proposal_hash(action: ProposedAction | ApprovalRequest) -> str:
    encoded = json.dumps(
        _proposal_fields(action),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ApprovalRequestService:
    """Create, resolve, expire, validate, and resume durable approval requests."""

    @staticmethod
    async def create_request(
        session: AsyncSession,
        run_id: str,
        action: ProposedAction,
        state: AssistantState | None = None,
        expires_in_seconds: int = 900,
    ) -> ApprovalRequest:
        if not action.requires_approval:
            raise ValueError("Approval request is only valid for actions requiring approval.")
        if expires_in_seconds <= 0:
            raise ValueError("expires_in_seconds must be positive")
        run = await RunPersistenceService.get_run(session, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        if run.status in {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }:
            raise ValueError(f"Cannot request approval for terminal run {run_id}")
        if state is not None and state.run_id != run_id:
            raise ValueError("Approval state run_id does not match run_id")

        proposal_hash = _proposal_hash(action)
        existing = await session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.run_id == run_id,
                ApprovalRequest.proposal_hash == proposal_hash,
                ApprovalRequest.status == "pending",
            )
        )
        if existing is None:
            clean_parameters = sanitize_payload(
                action.parameters,
                max_string_len=1_000,
                max_depth=8,
                max_items=100,
                max_payload_bytes=16_384,
            )
            approval = ApprovalRequest(
                run_id=run_id,
                action_type=sanitize_string(action.action_type, 64)[:64],
                description=sanitize_string(action.description, 2_000),
                target=None,
                important_arguments={"action_id": action.id},
                tool_name=sanitize_string(action.tool_name, 64)[:64] if action.tool_name else None,
                parameters=clean_parameters,
                risk_level=action.risk_level.value,
                status="pending",
                expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
                proposal_hash=proposal_hash,
            )
            try:
                async with session.begin_nested():
                    session.add(approval)
                    await session.flush()
            except IntegrityError:
                approval = await session.scalar(
                    select(ApprovalRequest).where(
                        ApprovalRequest.run_id == run_id,
                        ApprovalRequest.proposal_hash == proposal_hash,
                        ApprovalRequest.status == "pending",
                    )
                )
                if approval is None:
                    raise
        else:
            approval = existing

        checkpoint = state or await RunPersistenceService.load_state(session, run_id)
        if checkpoint is None:
            checkpoint = AssistantState(
                run_id=run_id,
                user_id=run.user_id,
                request=run.request,
                goal=run.goal,
                active_skill=run.active_skill,
                active_workflow=run.workflow_name,
                status=RunStatus.WAITING_APPROVAL,
            )
        else:
            proposed_actions = list(checkpoint.proposed_actions)
            if not any(item.id == action.id for item in proposed_actions):
                proposed_actions.append(action)
            checkpoint = checkpoint.model_copy(
                update={
                    "status": RunStatus.WAITING_APPROVAL,
                    "proposed_actions": proposed_actions,
                }
            )
        await RunPersistenceService.update_run_state(session, run_id, checkpoint)
        return approval

    @staticmethod
    async def get_request(session: AsyncSession, approval_id: str) -> ApprovalRequest | None:
        return await session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
        )

    @staticmethod
    async def validate_request(
        session: AsyncSession,
        approval_id: str,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        """Validate existence, expiry, and immutable proposal contents."""
        request = await ApprovalRequestService.get_request(session, approval_id)
        if request is None:
            raise ValueError(f"Approval request not found: {approval_id}")
        if request.status not in APPROVAL_STATUSES:
            raise ValueError(f"Unknown approval status: {request.status}")
        current_time = _utc(now) or datetime.now(UTC)
        expires_at = _utc(request.expires_at)
        if (
            request.status == "pending"
            and expires_at is not None
            and expires_at <= current_time
        ):
            _validate_approval_transition(request.status, "expired")
            request.status = "expired"
            request.approved = False
            request.reason = "Approval request expired."
            request.decided_at = current_time
            await session.flush()
            raise ValueError(f"Approval request expired: {approval_id}")
        if request.proposal_hash and request.proposal_hash != _proposal_hash(request):
            raise ValueError(f"Approval proposal has been modified: {approval_id}")
        return request

    @staticmethod
    async def decide(
        session: AsyncSession,
        approval_id: str,
        approved: bool,
        approver_id: str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        """Record one validated human decision with idempotent same-decision retries."""
        if not approver_id.strip():
            raise ValueError("approver_id is required")
        request = await session.scalar(
            select(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id)
            .with_for_update()
        )
        if request is None:
            raise ValueError(f"Approval request not found: {approval_id}")
        current_time = _utc(now) or datetime.now(UTC)
        expires_at = _utc(request.expires_at)
        if (
            request.status == "pending"
            and expires_at is not None
            and expires_at <= current_time
        ):
            _validate_approval_transition(request.status, "expired")
            request.status = "expired"
            request.approved = False
            request.reason = "Approval request expired."
            request.decided_at = current_time
            await session.flush()
            raise ValueError(f"Approval request expired: {approval_id}")
        await ApprovalRequestService.validate_request(session, approval_id, now=current_time)
        if request.status != "pending":
            if request.status == ("approved" if approved else "rejected"):
                return request
            raise ValueError(f"Approval request is already {request.status}")
        next_status = "approved" if approved else "rejected"
        _validate_approval_transition(request.status, next_status)
        request.status = next_status
        request.approved = approved
        request.approver_id = sanitize_string(approver_id, 36)[:36]
        request.reason = sanitize_string(reason, 2_000) if reason else None
        request.decided_at = current_time
        await session.flush()
        return request

    @staticmethod
    async def expire_pending(
        session: AsyncSession,
        now: datetime | None = None,
        limit: int = 500,
    ) -> int:
        """Transition expired pending approvals explicitly through the state machine."""
        if limit <= 0:
            return 0
        current_time = _utc(now) or datetime.now(UTC)
        rows = list(
            (
                await session.execute(
                    select(ApprovalRequest)
                    .where(
                        ApprovalRequest.status == "pending",
                        ApprovalRequest.expires_at.is_not(None),
                        ApprovalRequest.expires_at <= current_time,
                    )
                    .order_by(ApprovalRequest.expires_at, ApprovalRequest.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        for request in rows:
            _validate_approval_transition(request.status, "expired")
            request.status = "expired"
            request.approved = False
            request.reason = "Approval request expired."
            request.decided_at = current_time
        await session.flush()
        return len(rows)

    @staticmethod
    async def resume_approved(
        session: AsyncSession,
        approval_id: str,
        expected_version: int | None = None,
    ) -> AssistantState:
        """Rehydrate an approved action into its waiting run and move it to running."""
        request = await ApprovalRequestService.validate_request(session, approval_id)
        if request.status != "approved":
            raise ValueError(f"Approval request is not approved: {approval_id}")
        run = await RunPersistenceService.get_run(session, request.run_id)
        if run is None:
            raise ValueError(f"Run not found: {request.run_id}")
        state = await RunPersistenceService.load_state(session, request.run_id)
        if state is None:
            raise ValueError(f"Run checkpoint not found: {request.run_id}")

        action_id = str(request.important_arguments.get("action_id", request.id))
        approval = ActionApproval(
            action_id=action_id,
            approved=True,
            approver_id=request.approver_id or "unknown",
            reason=request.reason,
            decided_at=_utc(request.decided_at) or datetime.now(UTC),
        )
        approvals = list(state.approvals)
        if not any(item.action_id == approval.action_id for item in approvals):
            approvals.append(approval)
        continuation_context = dict(state.continuation_context)
        continuation_context["approved_request_id"] = request.id
        resumed_state = state.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "approvals": approvals,
                "continuation_context": continuation_context,
            }
        )
        if run.status == RunStatus.WAITING_APPROVAL.value:
            await RunPersistenceService.update_run_state(
                session,
                request.run_id,
                resumed_state,
                expected_version=expected_version,
            )
        elif run.status == RunStatus.RUNNING.value:
            if (
                any(item.action_id == approval.action_id for item in state.approvals)
                and state.continuation_context.get("approved_request_id") == request.id
            ):
                return state
            await RunPersistenceService.update_run_state(
                session,
                request.run_id,
                resumed_state,
                expected_version=expected_version,
            )
        else:
            raise ValueError(f"Run {request.run_id} cannot resume from {run.status}")
        return resumed_state

    # Short aliases keep the public service ergonomic without duplicating behavior.
    create = create_request
    resolve = decide
    resume = resume_approved


ApprovalService = ApprovalRequestService
