"""Conversations, their messages and the assistant runs that answer them."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.sanitization import sanitize_string
from app.infrastructure.db.base import Base, TimestampMixin
from app.infrastructure.db.models.common import (
    _JSONB,
    _sanitize_json_mapping,
)

if TYPE_CHECKING:
    from app.infrastructure.db.models.approvals import ApprovalRequest, UserQuestion
    from app.infrastructure.db.models.audit import (
        AuditEvent,
        AuditOutbox,
        LLMExecution,
        ToolExecution,
    )
    from app.infrastructure.db.models.users import User


class Conversation(Base, TimestampMixin):
    """Conversation session grouping messages and correlating runs."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="conversations")
    runs: Mapped[list["AssistantRun"]] = relationship("AssistantRun", back_populates="conversation")
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )

    @validates("metadata_")
    def sanitize_metadata(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_json_mapping(value)


class AssistantRun(Base, TimestampMixin):
    """Execution run record directly mapping to AssistantState & RunTelemetry."""

    __tablename__ = "assistant_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), index=True, nullable=True
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)

    # Correlation IDs (Separating mandatory internal ID from optional vendor tracing IDs)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    langsmith_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    langsmith_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    request: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)

    route_type: Mapped[str] = mapped_column(String(32), nullable=False)
    domains: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    complexity: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_skill: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    iteration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    react_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), default=Decimal("0.000000"), nullable=False
    )

    parallel_time_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Sanitized state snapshot (All sensitive/hidden thoughts recursively scrubbed)
    state_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    telemetry_degraded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Knowledge answers (migration 0012): link to the retrieval trace and the
    # final SUFFICIENT/PARTIAL/INSUFFICIENT status; evidence in run_evidence.
    retrieval_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    answer_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="runs")
    conversation: Mapped["Conversation | None"] = relationship(
        "Conversation", back_populates="runs"
    )
    tool_executions: Mapped[list["ToolExecution"]] = relationship(
        "ToolExecution", back_populates="run", cascade="all, delete-orphan"
    )
    llm_executions: Mapped[list["LLMExecution"]] = relationship(
        "LLMExecution", back_populates="run", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        "AuditEvent", back_populates="run", cascade="all, delete-orphan"
    )
    approval_requests: Mapped[list["ApprovalRequest"]] = relationship(
        "ApprovalRequest", back_populates="run", cascade="all, delete-orphan"
    )
    user_questions: Mapped[list["UserQuestion"]] = relationship(
        "UserQuestion", back_populates="run"
    )
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(
        "WorkflowRun", back_populates="run", cascade="all, delete-orphan"
    )
    audit_outbox_entries: Mapped[list["AuditOutbox"]] = relationship(
        "AuditOutbox", back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_runs_user_created", "user_id", "created_at"),
        Index("ix_runs_session", "session_id"),
        Index("ix_runs_correlation", "correlation_id"),
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting_input', 'waiting_approval', "
            "'completed', 'failed', 'cancelled')",
            name="ck_assistant_runs_status",
        ),
        CheckConstraint(
            "(status IN ('completed', 'failed', 'cancelled') AND completed_at IS NOT NULL) "
            "OR (status NOT IN ('completed', 'failed', 'cancelled') AND completed_at IS NULL)",
            name="ck_assistant_runs_completed_at",
        ),
    )
    __mapper_args__ = {"version_id_col": state_version}


class RunEvidence(Base):
    """One evidence unit sent to answer synthesis for a run (immutable snapshot)."""

    __tablename__ = "run_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assistant_runs.id", ondelete="CASCADE"), nullable=False
    )
    retrieval_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str] = mapped_column(String(16), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    primary_chunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_ids: Mapped[list[str]] = mapped_column(_JSONB, default=list, nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    citation_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        Index("ix_run_evidence_run", "run_id", "rank"),
        Index("ix_run_evidence_document", "document_version_id"),
    )


class Message(Base):
    """Individual conversation message with sanitized content."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("assistant_runs.id"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

    __table_args__ = (Index("ix_messages_conv_created", "conversation_id", "created_at"),)

    @validates("metadata_")
    def sanitize_metadata(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_json_mapping(value)

    @validates("content")
    def sanitize_content(self, _key: str, value: str) -> str:
        return sanitize_string(value, max_string_len=20_000)


class WorkflowRun(Base):
    """Workflow execution instance record."""

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_runs.id"), nullable=False)
    workflow_name: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(16), default="1.0.0", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    executed_node_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["AssistantRun"] = relationship("AssistantRun", back_populates="workflow_runs")
