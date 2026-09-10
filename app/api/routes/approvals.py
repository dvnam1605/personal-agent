"""FastAPI routes for Human-in-the-Loop approval plane (spec P18-04)."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.domain.enums import ApprovalOutcome
from app.infrastructure.db.session import get_db_session
from app.services.approvals import ApprovalRequestService, execution_token_for_request
from app.services.approvals.execution import ApprovalExecutionResult, ApprovalExecutionService
from app.services.platform.run_persistence import RunPersistenceService

router = APIRouter(tags=["approvals"])

_execution_service: ApprovalExecutionService | None = None


def get_approval_execution_service() -> ApprovalExecutionService:
    """Process-wide executor so tests can override Calendar/Gmail tool wiring."""
    global _execution_service
    if _execution_service is None:
        _execution_service = ApprovalExecutionService()
    return _execution_service


class ApprovalRequestResponse(BaseModel):
    """Public read representation of an ApprovalRequest row."""

    model_config = ConfigDict(extra="ignore")

    id: str
    run_id: str
    action_type: str
    description: str
    target: str | None = None
    important_arguments: dict[str, Any] = Field(default_factory=dict)
    tool_name: str | None = None
    risk_level: str
    status: str
    expires_at: datetime | None = None
    created_at: datetime
    approved: bool | None = None
    approver_id: str | None = None
    reason: str | None = None


class ApproveRequestBody(BaseModel):
    """Optional request body when approving an action."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None,
        description="Optional human approval note or justification.",
    )
    execute: bool = Field(
        default=False,
        description=(
            "When true, mint the one-shot token and run the bound Calendar/Gmail tool "
            "in this request. The token is consumed and not returned."
        ),
    )


class ApproveResponse(BaseModel):
    """Response returned upon approving a mutation action.

    ``token`` is returned only on the first successful mint. A retry of an
    already-approved request is HTTP 409 — the ciphertext is write-once and
    cannot be recovered from ``token_hash``.
    """

    model_config = ConfigDict(extra="forbid")

    approval_id: str
    status: str
    outcome: str
    approved: bool
    token: str | None = Field(
        default=None,
        description=(
            "HMAC execution token, present only on the first approve when execute is false. "
            "Store it client-side; a later POST returns 409 instead of reminting."
        ),
    )
    executed: bool = False
    execution: dict[str, Any] | None = None
    error: str | None = None


class ExecuteApprovalBody(BaseModel):
    """Request body when consuming a previously minted execution token."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1, description="HMAC token from the first approve.")


class DenyRequestBody(BaseModel):
    """Request body when rejecting or cancelling an approval request."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None,
        description="Optional rejection or cancellation reason.",
    )
    outcome: ApprovalOutcome = Field(
        default=ApprovalOutcome.REJECTED,
        description="Outcome classification: rejected, cancelled, or unavailable.",
    )


class DenyResponse(BaseModel):
    """Response returned upon denying an approval request."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str
    status: str
    outcome: str
    approved: bool
    reason: str | None = None


def _execution_payload(result: ApprovalExecutionResult) -> dict[str, Any]:
    payload = dict(result.output or {})
    payload["message"] = result.message
    return payload


@router.get(
    "/approvals/pending",
    response_model=list[ApprovalRequestResponse],
    summary="List pending approval requests",
)
async def list_pending_approvals(
    run_id: str | None = Query(default=None, description="Optional run_id filter"),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[ApprovalRequestResponse]:
    """Retrieve all pending approval requests awaiting human confirmation for current user (spec P18-04, M4)."""
    rows = await ApprovalRequestService.get_pending_requests(
        session, run_id=run_id, user_id=user_id
    )
    return [
        ApprovalRequestResponse(
            id=r.id,
            run_id=r.run_id,
            action_type=r.action_type,
            description=r.description,
            target=r.target,
            important_arguments=r.important_arguments
            if isinstance(r.important_arguments, dict)
            else {},
            tool_name=r.tool_name,
            risk_level=r.risk_level,
            status=r.status,
            expires_at=r.expires_at,
            created_at=r.created_at,
            approved=r.approved,
            approver_id=r.approver_id,
            reason=r.reason,
        )
        for r in rows
    ]


@router.post(
    "/approvals/{id}/approve",
    response_model=ApproveResponse,
    summary="Approve a pending mutation action",
    responses={
        409: {
            "description": (
                "Execution token already issued for this approval. "
                "It is not returned again; reuse the token from the first 200."
            )
        }
    },
)
async def approve_request(
    id: str = Path(..., description="Approval request ID"),
    body: ApproveRequestBody | None = None,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    execution_service: ApprovalExecutionService = Depends(  # noqa: B008
        get_approval_execution_service
    ),
) -> ApproveResponse:
    """Approve a pending mutation action, returning a signed HMAC execution token (P18-06, H2, M4).

    The plaintext token is issued once. A second approve of the same request
    returns HTTP 409 so clients cannot treat ``token: null`` as a usable retry.
    Pass ``execute: true`` to consume the token immediately against Calendar/Gmail.
    """
    req = await ApprovalRequestService.get_request(session, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request not found: {id}",
        )
    run = await RunPersistenceService.get_run(session, req.run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run associated with approval request not found: {req.run_id}",
        )
    if run.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Approval request does not belong to the current user",
        )

    try:
        decided = await ApprovalRequestService.decide_with_outcome(
            session=session,
            approval_id=id,
            outcome=ApprovalOutcome.ALLOWED_ONCE,
            approver_id=user_id,
            reason=body.reason if body else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    token = None
    if decided.tool_name and decided.token_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Execution token already issued; it is not returned again.",
        )
    if decided.tool_name and not decided.token_hash:
        try:
            token = execution_token_for_request(decided, user_id=user_id)
            decided.token_hash = sha256(token.encode("utf-8")).hexdigest()
            await session.flush()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to mint execution token.",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to mint execution token.",
            ) from exc

    executed = False
    execution: dict[str, Any] | None = None
    error: str | None = None
    response_token = token
    if body is not None and body.execute and token:
        try:
            result = await execution_service.execute(session, decided, token, user_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        executed = result.success
        execution = _execution_payload(result)
        error = result.error
        response_token = None

    return ApproveResponse(
        approval_id=decided.id,
        status=decided.status,
        outcome=ApprovalOutcome.ALLOWED_ONCE.value,
        approved=True,
        token=response_token,
        executed=executed,
        execution=execution,
        error=error,
    )


@router.post(
    "/approvals/{id}/execute",
    response_model=ApproveResponse,
    summary="Execute an already-approved mutation with its one-shot token",
)
async def execute_approved_request(
    body: ExecuteApprovalBody,
    id: str = Path(..., description="Approval request ID"),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    execution_service: ApprovalExecutionService = Depends(  # noqa: B008
        get_approval_execution_service
    ),
) -> ApproveResponse:
    """Consume a previously minted token and run the bound Calendar/Gmail tool."""
    req = await ApprovalRequestService.get_request(session, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request not found: {id}",
        )
    run = await RunPersistenceService.get_run(session, req.run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run associated with approval request not found: {req.run_id}",
        )
    if run.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Approval request does not belong to the current user",
        )
    if req.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Approval request is not approved: {id}",
        )
    try:
        result = await execution_service.execute(session, req, body.token, user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return ApproveResponse(
        approval_id=req.id,
        status=req.status,
        outcome=ApprovalOutcome.ALLOWED_ONCE.value,
        approved=True,
        token=None,
        executed=result.success,
        execution=_execution_payload(result),
        error=result.error,
    )


@router.post(
    "/approvals/{id}/deny",
    response_model=DenyResponse,
    summary="Deny or cancel a pending approval request",
)
async def deny_request(
    id: str = Path(..., description="Approval request ID"),
    body: DenyRequestBody | None = None,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DenyResponse:
    """Explicitly reject, cancel, or mark unavailable a pending approval request (M4)."""
    req = await ApprovalRequestService.get_request(session, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request not found: {id}",
        )
    run = await RunPersistenceService.get_run(session, req.run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run associated with approval request not found: {req.run_id}",
        )
    if run.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Approval request does not belong to the current user",
        )

    outcome = body.outcome if body else ApprovalOutcome.REJECTED
    reason = body.reason if body else None
    try:
        decided = await ApprovalRequestService.decide_with_outcome(
            session=session,
            approval_id=id,
            outcome=outcome,
            approver_id=user_id,
            reason=reason,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return DenyResponse(
        approval_id=decided.id,
        status=decided.status,
        outcome=outcome.value,
        approved=False,
        reason=decided.reason,
    )
