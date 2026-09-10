"""Question Plane service for interactive user clarifications (spec P18-02A).

Handles question creation, options validation, multi-select constraints,
atomic answer recording, and cancellation without framework dependencies (spec §18A.5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sanitization import sanitize_payload, sanitize_string
from app.domain.models import (
    UserQuestionAnswer,
    UserQuestionItem,
)
from app.infrastructure.db.models import AssistantRun, UserQuestion


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class QuestionPlaneService:
    """Manage lifecycle and validation for interactive user questions."""

    @staticmethod
    async def create_question_request(
        session: AsyncSession,
        run_id: str | None,
        questions: list[UserQuestionItem],
        task_id: str | None = None,
        expires_in_seconds: int = 900,
    ) -> UserQuestion:
        """Create and persist a batch of structured clarification questions."""
        if not questions:
            raise ValueError("At least one question item is required.")
        if expires_in_seconds <= 0:
            raise ValueError("expires_in_seconds must be positive.")

        serialized_questions = [
            sanitize_payload(q.model_dump(), max_string_len=4000) for q in questions
        ]

        question_row = UserQuestion(
            run_id=sanitize_string(run_id, 36)[:36] if run_id else None,
            task_id=sanitize_string(task_id, 64)[:64] if task_id else None,
            questions=serialized_questions,
            status="pending",
            answers=None,
            answered_by=None,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
            version=1,
        )
        session.add(question_row)
        await session.flush()
        return question_row

    @staticmethod
    async def get_question_request(
        session: AsyncSession,
        question_id: str,
    ) -> UserQuestion | None:
        """Retrieve question row by unique identifier."""
        return await session.scalar(select(UserQuestion).where(UserQuestion.id == question_id))

    @staticmethod
    async def get_pending_questions(
        session: AsyncSession,
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> list[UserQuestion]:
        """List pending user questions, optionally filtered by run_id and user_id (spec P18-04, M4)."""
        stmt = select(UserQuestion).where(UserQuestion.status == "pending")
        if run_id:
            stmt = stmt.where(UserQuestion.run_id == run_id)
        if user_id:
            stmt = stmt.join(AssistantRun, UserQuestion.run_id == AssistantRun.id).where(
                AssistantRun.user_id == user_id
            )
        stmt = stmt.order_by(UserQuestion.created_at).limit(100)
        return list((await session.execute(stmt)).scalars())

    @staticmethod
    async def record_answers(
        session: AsyncSession,
        question_id: str,
        answers: list[UserQuestionAnswer],
        answered_by: str,
        now: datetime | None = None,
        expected_user_id: str | None = None,
        expected_version: int | None = None,
    ) -> UserQuestion:
        """Validate and record user answers against question options and constraints."""
        if not answered_by.strip():
            raise ValueError("answered_by is required.")
        if not answers:
            raise ValueError("At least one answer item is required.")

        question = await session.scalar(
            select(UserQuestion).where(UserQuestion.id == question_id).with_for_update()
        )
        if question is None:
            raise ValueError(f"UserQuestion not found: {question_id}")

        await QuestionPlaneService._assert_owner(session, question, expected_user_id)

        if expected_version is not None and question.version != expected_version:
            raise ValueError(
                f"Version conflict: question {question_id} is at version "
                f"{question.version}, expected {expected_version}."
            )

        current_time = _utc(now) or datetime.now(UTC)
        expires_at = _utc(question.expires_at)
        if question.status == "pending" and expires_at is not None and expires_at <= current_time:
            question.status = "expired"
            await session.flush()
            raise ValueError(f"UserQuestion request expired: {question_id}")

        if question.status != "pending":
            if question.status == "answered":
                serialized_answers = [
                    sanitize_payload(a.model_dump(), max_string_len=4000) for a in answers
                ]
                if question.answers != serialized_answers:
                    raise ValueError(
                        f"Conflict: Question request {question_id} is already answered with different answers."
                    )
                return question
            raise ValueError(f"UserQuestion is already {question.status}")

        # Validate answers against declared question options
        question_items_by_id: dict[str, dict[str, Any]] = {
            item["id"]: item for item in question.questions if isinstance(item, dict)
        }

        for ans in answers:
            item = question_items_by_id.get(ans.question_id)
            if item is None:
                raise ValueError(f"Answer references unknown question item: {ans.question_id}")

            raw_options = item.get("options")
            multi_select = bool(item.get("multi_select", False))

            if raw_options:
                valid_labels = {
                    opt["label"] if isinstance(opt, dict) else opt for opt in raw_options
                }
                for sel in ans.selected_options:
                    if sel not in valid_labels:
                        raise ValueError(
                            f"Selected option '{sel}' is not valid for question '{ans.question_id}'. "
                            f"Allowed: {sorted(valid_labels)}"
                        )

                if not multi_select and len(ans.selected_options) > 1:
                    raise ValueError(
                        f"Question '{ans.question_id}' does not allow multiple selections "
                        f"(multi_select=False, received {len(ans.selected_options)})."
                    )

        serialized_answers = [
            sanitize_payload(a.model_dump(), max_string_len=4000) for a in answers
        ]

        question.answers = serialized_answers
        question.status = "answered"
        question.answered_by = sanitize_string(answered_by, 36)[:36]
        question.answered_at = current_time
        question.version = int(question.version or 1) + 1
        await session.flush()
        return question

    @staticmethod
    async def cancel_question(
        session: AsyncSession,
        question_id: str,
        reason: str | None = None,
        expected_user_id: str | None = None,
        expected_version: int | None = None,
    ) -> UserQuestion:
        """Cancel a pending question request."""
        question = await session.scalar(
            select(UserQuestion).where(UserQuestion.id == question_id).with_for_update()
        )
        if question is None:
            raise ValueError(f"UserQuestion not found: {question_id}")

        await QuestionPlaneService._assert_owner(session, question, expected_user_id)

        if expected_version is not None and question.version != expected_version:
            raise ValueError(
                f"Version conflict: question {question_id} is at version "
                f"{question.version}, expected {expected_version}."
            )

        if question.status != "pending":
            return question

        question.status = "cancelled"
        question.version = int(question.version or 1) + 1
        await session.flush()
        return question

    @staticmethod
    async def _assert_owner(
        session: AsyncSession,
        question: UserQuestion,
        expected_user_id: str | None,
    ) -> None:
        """Fail closed: orphan questions (no run) and mismatched owners are forbidden."""
        if not question.run_id:
            raise PermissionError("Question request does not belong to expected user.")
        if expected_user_id is None:
            from app.core.config import settings

            if settings.auth_enforced:
                raise PermissionError("expected_user_id is required to mutate a question request.")
            return
        from app.services.run_persistence import RunPersistenceService

        run = await RunPersistenceService.get_run(session, question.run_id)
        if run is None or run.user_id != expected_user_id:
            raise PermissionError("Question request does not belong to expected user.")


__all__ = ["QuestionPlaneService"]
