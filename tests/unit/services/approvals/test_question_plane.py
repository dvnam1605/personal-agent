"""Unit tests for Question Plane service and models (spec P18-02A)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.models import (
    UserQuestionAnswer,
    UserQuestionItem,
    UserQuestionOption,
)
from app.infrastructure.db.base import Base
from app.services.approvals.question_plane import QuestionPlaneService


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_get_question_request(db_session: AsyncSession) -> None:
    questions = [
        UserQuestionItem(
            id="q1",
            question="Which time slot works best?",
            options=[
                UserQuestionOption(label="10:00 AM", description="Morning meeting"),
                UserQuestionOption(label="2:00 PM", description="Afternoon meeting"),
            ],
            multi_select=False,
        )
    ]
    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id="run_100",
        questions=questions,
    )
    assert created.id is not None
    assert created.status == "pending"
    assert len(created.questions) == 1
    assert created.version == 1

    fetched = await QuestionPlaneService.get_question_request(db_session, created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.run_id == "run_100"


@pytest.mark.asyncio
async def test_record_answers_valid_option(db_session: AsyncSession) -> None:
    questions = [
        UserQuestionItem(
            id="q1",
            question="Do you approve this draft?",
            options=[
                UserQuestionOption(label="Yes"),
                UserQuestionOption(label="No"),
            ],
            multi_select=False,
            intent="plan-review",
        )
    ]
    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id="run_101",
        questions=questions,
    )

    answers = [UserQuestionAnswer(question_id="q1", selected_options=["Yes"], free_text="Ship it")]
    answered = await QuestionPlaneService.record_answers(
        db_session,
        question_id=created.id,
        answers=answers,
        answered_by="user_alice",
    )
    assert answered.status == "answered"
    assert answered.answered_by == "user_alice"
    assert answered.answers is not None
    assert answered.answers[0]["selected_options"] == ["Yes"]
    assert answered.answers[0]["free_text"] == "Ship it"
    assert answered.version == 2


@pytest.mark.asyncio
async def test_record_answers_rejects_stale_expected_version(db_session: AsyncSession) -> None:
    questions = [
        UserQuestionItem(
            id="q1",
            question="Do you approve this draft?",
            options=[UserQuestionOption(label="Yes"), UserQuestionOption(label="No")],
        )
    ]
    created = await QuestionPlaneService.create_question_request(
        db_session, run_id="run_ver", questions=questions
    )
    with pytest.raises(ValueError, match="Version conflict"):
        await QuestionPlaneService.record_answers(
            db_session,
            question_id=created.id,
            answers=[UserQuestionAnswer(question_id="q1", selected_options=["Yes"])],
            answered_by="user_alice",
            expected_version=7,
        )


@pytest.mark.asyncio
async def test_record_answers_invalid_option_rejected(db_session: AsyncSession) -> None:
    questions = [
        UserQuestionItem(
            id="q1",
            question="Choose a priority",
            options=[
                UserQuestionOption(label="Low"),
                UserQuestionOption(label="High"),
            ],
            multi_select=False,
        )
    ]
    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id="run_102",
        questions=questions,
    )

    invalid_answers = [UserQuestionAnswer(question_id="q1", selected_options=["Critical"])]
    with pytest.raises(ValueError, match="is not valid for question"):
        await QuestionPlaneService.record_answers(
            db_session,
            question_id=created.id,
            answers=invalid_answers,
            answered_by="user_alice",
        )


@pytest.mark.asyncio
async def test_record_answers_single_select_violation_rejected(db_session: AsyncSession) -> None:
    questions = [
        UserQuestionItem(
            id="q1",
            question="Pick one",
            options=[
                UserQuestionOption(label="Option A"),
                UserQuestionOption(label="Option B"),
            ],
            multi_select=False,
        )
    ]
    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id="run_103",
        questions=questions,
    )

    multiple_answers = [
        UserQuestionAnswer(question_id="q1", selected_options=["Option A", "Option B"])
    ]
    with pytest.raises(ValueError, match="does not allow multiple selections"):
        await QuestionPlaneService.record_answers(
            db_session,
            question_id=created.id,
            answers=multiple_answers,
            answered_by="user_alice",
        )


@pytest.mark.asyncio
async def test_record_answers_multi_select_allowed(db_session: AsyncSession) -> None:
    questions = [
        UserQuestionItem(
            id="q1",
            question="Select all applicable tags",
            options=[
                UserQuestionOption(label="Bug"),
                UserQuestionOption(label="Frontend"),
                UserQuestionOption(label="Urgent"),
            ],
            multi_select=True,
        )
    ]
    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id="run_104",
        questions=questions,
    )

    multi_answers = [UserQuestionAnswer(question_id="q1", selected_options=["Bug", "Urgent"])]
    answered = await QuestionPlaneService.record_answers(
        db_session,
        question_id=created.id,
        answers=multi_answers,
        answered_by="user_alice",
    )
    assert answered.status == "answered"
    assert answered.answers is not None
    assert len(answered.answers[0]["selected_options"]) == 2


@pytest.mark.asyncio
async def test_cancel_question(db_session: AsyncSession) -> None:
    questions = [UserQuestionItem(id="q1", question="Are you there?")]
    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id="run_105",
        questions=questions,
    )
    cancelled = await QuestionPlaneService.cancel_question(db_session, created.id)
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_expired_question_rejected(db_session: AsyncSession) -> None:
    questions = [UserQuestionItem(id="q1", question="Time sensitive?")]
    created = await QuestionPlaneService.create_question_request(
        db_session,
        run_id="run_106",
        questions=questions,
        expires_in_seconds=10,
    )
    answers = [UserQuestionAnswer(question_id="q1", free_text="yes")]
    future_time = datetime.now(UTC) + timedelta(seconds=100)

    with pytest.raises(ValueError, match="request expired"):
        await QuestionPlaneService.record_answers(
            db_session,
            question_id=created.id,
            answers=answers,
            answered_by="user_alice",
            now=future_time,
        )
