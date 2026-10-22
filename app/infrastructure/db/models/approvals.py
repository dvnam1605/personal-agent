"""Human-in-the-loop records: mutation approvals and clarification questions."""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.sanitization import sanitize_string
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.common import (
    _sanitize_approval_arguments,
    _sanitize_bounded_text,
)

if TYPE_CHECKING:
    from app.infrastructure.db.models.conversations import AssistantRun


class ApprovalRequest(Base):
    """Human-in-the-loop approval request for mutation action."""

    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_runs.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    important_arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proposal_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    approver_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["AssistantRun"] = relationship("AssistantRun", back_populates="approval_requests")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled', 'unavailable')",
            name="ck_approval_requests_status",
        ),
        CheckConstraint(
            "(status = 'pending' AND approved IS NULL) "
            "OR (status = 'approved' AND approved = true) "
            "OR (status IN ('rejected', 'expired', 'cancelled', 'unavailable') AND approved = false)",
            name="ck_approval_requests_decision_consistency",
        ),
        Index("ix_approval_requests_run_status", "run_id", "status"),
        Index(
            "uq_approval_requests_pending_proposal",
            "run_id",
            "proposal_hash",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    @validates("important_arguments", "parameters")
    def sanitize_arguments(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_approval_arguments(value)

    @validates("description", "target", "reason")
    def sanitize_text(self, _key: str, value: str | None) -> str | None:
        return None if value is None else sanitize_string(value, max_string_len=2_000)

    @validates("action_type", "tool_name", "risk_level")
    def sanitize_bounded_text(self, _key: str, value: str | None) -> str | None:
        if value is None:
            return None
        return _sanitize_bounded_text(value, 64)


class UserQuestion(Base):
    """Question Plane model for interactive human clarification (P18-02A)."""

    __tablename__ = "user_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("assistant_runs.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    answers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    answered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)

    run: Mapped["AssistantRun | None"] = relationship(
        "AssistantRun", back_populates="user_questions"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'answered', 'cancelled', 'expired')",
            name="ck_user_questions_status",
        ),
        Index("ix_user_questions_run_status", "run_id", "status"),
    )
