"""Question Plane domain models for structured human interaction (spec P18-02A).

Provides typed question requests, options, intent framing, and structured answers,
distinct from binary permission approvals.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserQuestionOption(BaseModel):
    """Selectable option for multiple-choice clarification questions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(
        ...,
        min_length=1,
        description="Option text displayed to user and submitted as selected answer.",
    )
    description: str | None = Field(
        default=None,
        description="Optional explanatory text providing context for this option.",
    )


class UserQuestionItem(BaseModel):
    """An individual question within an AskUserQuestionRequest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the question item.",
    )
    question: str = Field(
        ...,
        min_length=1,
        description="The primary question prompt presented to the user.",
    )
    detail: str | None = Field(
        default=None,
        description="Optional Markdown or multi-line detailed background context.",
    )
    options: list[UserQuestionOption] | None = Field(
        default=None,
        description="Optional list of choices for multiple choice / single choice questions.",
    )
    multi_select: bool = Field(
        default=False,
        description="If True, multiple options can be selected; otherwise at most one.",
    )
    intent: Literal["plan-review"] | None = Field(
        default=None,
        description="Special UI intent framing (e.g. 'plan-review' for plan approval feedback).",
    )


class AskUserQuestionRequest(BaseModel):
    """Structured question request emitted by agents via tool_ask_user (P18-02A)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the question request batch.",
    )
    run_id: str | None = Field(
        default=None,
        description="Associated AssistantRun identifier, if invoked during a run.",
    )
    task_id: str | None = Field(
        default=None,
        description="Associated task identifier within a workflow or DAG.",
    )
    questions: list[UserQuestionItem] = Field(
        ...,
        min_length=1,
        description="List of structured question items to present to the user.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the question request was created.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional UTC timestamp when the question request expires.",
    )

    @field_validator("created_at", "expires_at", mode="after")
    @classmethod
    def ensure_utc_aware(cls, v: datetime | None) -> datetime | None:
        """Enforce UTC-aware timestamp."""
        if v is None:
            return None
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("Timestamp must be UTC-aware (e.g. datetime.now(UTC)).")
        return v


class UserQuestionAnswer(BaseModel):
    """User response for a specific question item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str = Field(
        ...,
        min_length=1,
        description="Identifier of the question item being answered.",
    )
    selected_options: list[str] = Field(
        default_factory=list,
        description="Labels of selected options (empty for free-text answers).",
    )
    free_text: str | None = Field(
        default=None,
        description="User-supplied free-form text input or feedback.",
    )


class UserQuestionResponse(BaseModel):
    """Completed response payload for an AskUserQuestionRequest batch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(
        ...,
        min_length=1,
        description="Identifier of the answered AskUserQuestionRequest.",
    )
    answers: list[UserQuestionAnswer] = Field(
        ...,
        min_length=1,
        description="Submitted answers corresponding to question items.",
    )
    answered_by: str = Field(
        ...,
        min_length=1,
        description="Identity of the answering user.",
    )
    answered_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the response was submitted.",
    )

    @field_validator("answered_at", mode="after")
    @classmethod
    def ensure_utc_aware(cls, v: datetime) -> datetime:
        """Enforce UTC-aware timestamp."""
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("Timestamp must be UTC-aware (e.g. datetime.now(UTC)).")
        return v


__all__ = [
    "AskUserQuestionRequest",
    "UserQuestionAnswer",
    "UserQuestionItem",
    "UserQuestionOption",
    "UserQuestionResponse",
]
