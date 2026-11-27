"""FastAPI route for answering the structured questions an agent run asks the user."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.domain.models import UserQuestionAnswer
from app.infrastructure.db.session import get_db_session
from app.services.approvals.question_plane import QuestionPlaneService
from app.services.platform.run_persistence import RunPersistenceService

router = APIRouter(tags=["questions"])

QUESTION_NOT_FOUND = "Không tìm thấy câu hỏi."


class AnswerQuestionRequestBody(BaseModel):
    """Request payload containing answers to a structured question request."""

    model_config = ConfigDict(extra="forbid")

    answers: list[UserQuestionAnswer] = Field(
        ...,
        min_length=1,
        description="List of submitted answers corresponding to question items.",
    )
    expected_version: int = Field(
        ...,
        ge=1,
        description="Optimistic concurrency token from the last observed question version.",
    )


class AnswerQuestionResponse(BaseModel):
    """Response returned upon successfully recording question answers."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    status: str
    answered_by: str
    answers: list[dict[str, Any]]
    version: int


@router.post(
    "/questions/{id}/answer",
    response_model=AnswerQuestionResponse,
    summary="Submit answers for an interactive question request",
)
async def answer_question(
    body: AnswerQuestionRequestBody,
    id: str = Path(..., description="UserQuestion ID"),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AnswerQuestionResponse:
    """Record the caller's answers to a pending question on one of their runs.

    A question that is missing, has no run, or belongs to another user's run all
    answer the same 404 so ids cannot be probed. A stale ``expected_version``
    answers 409.
    """
    q = await QuestionPlaneService.get_question_request(session, id)
    run = (
        await RunPersistenceService.get_run(session, q.run_id)
        if q is not None and q.run_id
        else None
    )
    if run is None or run.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUESTION_NOT_FOUND)

    try:
        answered = await QuestionPlaneService.record_answers(
            session=session,
            question_id=id,
            answers=body.answers,
            answered_by=user_id,
            expected_user_id=user_id,
            expected_version=body.expected_version,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=QUESTION_NOT_FOUND
        ) from exc
    except ValueError as exc:
        detail = str(exc)
        code = (
            status.HTTP_409_CONFLICT
            if detail.startswith("Version conflict")
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail) from exc

    return AnswerQuestionResponse(
        question_id=answered.id,
        status=answered.status,
        answered_by=answered.answered_by or user_id,
        answers=answered.answers if isinstance(answered.answers, list) else [],
        version=int(answered.version or 1),
    )
