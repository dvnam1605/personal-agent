"""Approve-and-execute or deny an approval on behalf of its owner, outside the HTTP route.

Used by chat channels (Telegram) that cannot call the approvals API with a session
header. The checks match ``POST /approvals/{id}/approve``: the run must belong to the
caller, the request must still be pending, and the one-shot token is minted, hashed
and consumed in the same step so it never leaves the server.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ApprovalOutcome
from app.services.approvals import ApprovalRequestService, execution_token_for_request
from app.services.approvals.execution import ApprovalExecutionService
from app.services.platform.run_persistence import RunPersistenceService


@dataclass(frozen=True)
class DecisionResult:
    """What happened, in words fit for the user."""

    ok: bool
    message: str


async def _owned(session: AsyncSession, approval_id: str, user_id: str) -> bool:
    request = await ApprovalRequestService.get_request(session, approval_id)
    if request is None:
        return False
    run = await RunPersistenceService.get_run(session, request.run_id)
    return run is not None and run.user_id == user_id


async def approve_and_execute(
    session: AsyncSession,
    approval_id: str,
    user_id: str,
    execution_service: ApprovalExecutionService,
) -> DecisionResult:
    """Approve the caller's pending request and run it once."""
    if not await _owned(session, approval_id, user_id):
        return DecisionResult(False, "Không tìm thấy yêu cầu này.")
    try:
        decided = await ApprovalRequestService.decide_with_outcome(
            session=session,
            approval_id=approval_id,
            outcome=ApprovalOutcome.ALLOWED_ONCE,
            approver_id=user_id,
            reason="approved from chat",
        )
    except ValueError:
        return DecisionResult(False, "Yêu cầu này đã được xử lý hoặc hết hạn.")
    if not decided.tool_name or decided.token_hash:
        return DecisionResult(False, "Yêu cầu này đã được duyệt trước đó.")
    token = execution_token_for_request(decided, user_id=user_id)
    decided.token_hash = sha256(token.encode("utf-8")).hexdigest()
    await session.flush()
    try:
        result = await execution_service.execute(session, decided, token, user_id)
    except ValueError:
        return DecisionResult(False, "Không thực thi được yêu cầu đã duyệt.")
    return DecisionResult(result.success, result.message)


async def deny(session: AsyncSession, approval_id: str, user_id: str) -> DecisionResult:
    """Reject the caller's pending request."""
    if not await _owned(session, approval_id, user_id):
        return DecisionResult(False, "Không tìm thấy yêu cầu này.")
    try:
        await ApprovalRequestService.decide_with_outcome(
            session=session,
            approval_id=approval_id,
            outcome=ApprovalOutcome.REJECTED,
            approver_id=user_id,
            reason="rejected from chat",
        )
    except ValueError:
        return DecisionResult(False, "Yêu cầu này đã được xử lý hoặc hết hạn.")
    return DecisionResult(True, "Đã từ chối.")
