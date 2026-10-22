"""Append-only execution audit (tool and LLM calls, audit events) and the audit outbox."""

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
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.common import (
    _sanitize_bounded_text,
    _sanitize_json_mapping,
)

if TYPE_CHECKING:
    from app.infrastructure.db.models.conversations import AssistantRun


class ToolExecution(Base):
    """Append-only audit record of tool invocation with sanitized parameters."""

    __tablename__ = "tool_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    outbox_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_runs.id"), nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    cached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    run: Mapped["AssistantRun"] = relationship("AssistantRun", back_populates="tool_executions")

    __table_args__ = (
        Index("ix_tool_exec_run", "run_id"),
        Index("ix_tool_exec_name_time", "tool_name", "executed_at"),
    )

    @validates("input_parameters", "output_summary")
    def sanitize_parameters(self, _key: str, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _sanitize_json_mapping(value)

    @validates("agent_name", "tool_name")
    def sanitize_names(self, _key: str, value: str | None) -> str | None:
        return _sanitize_bounded_text(value, 64)


class LLMExecution(Base):
    """Append-only audit record of LLM invocation (no hidden chain-of-thought)."""

    __tablename__ = "llm_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    outbox_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_runs.id"), nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), default=Decimal("0.000000"), nullable=False
    )
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    run: Mapped["AssistantRun"] = relationship("AssistantRun", back_populates="llm_executions")

    __table_args__ = (
        Index("ix_llm_exec_run", "run_id"),
        Index("ix_llm_exec_model", "provider", "model"),
    )

    @validates("agent_name", "provider", "model", "purpose")
    def sanitize_names(self, _key: str, value: str | None) -> str | None:
        max_len = {"provider": 32, "model": 64, "purpose": 64}.get(_key, 64)
        return _sanitize_bounded_text(value, max_len)


class AuditEvent(Base):
    """System-wide security and policy audit event."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    outbox_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("assistant_runs.id"), nullable=True
    )
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    run: Mapped["AssistantRun"] = relationship("AssistantRun", back_populates="audit_events")

    __table_args__ = (
        Index("ix_audit_events_created", "created_at"),
        Index("ix_audit_events_type", "event_type"),
    )

    @validates("payload")
    def sanitize_event_payload(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_json_mapping(value)

    @validates("event_type", "component", "severity")
    def sanitize_names(self, _key: str, value: str | None) -> str | None:
        max_len = 16 if _key == "severity" else 64
        return _sanitize_bounded_text(value, max_len)


class AuditOutbox(Base):
    """Durable, sanitized audit event awaiting materialization into an audit projection."""

    __tablename__ = "audit_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("assistant_runs.id"), nullable=True
    )
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["AssistantRun | None"] = relationship(
        "AssistantRun", back_populates="audit_outbox_entries"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'delivered', 'failed')",
            name="ck_audit_outbox_status",
        ),
        Index("ix_audit_outbox_pending", "status", "created_at"),
        Index("ix_audit_outbox_run", "run_id"),
        Index("ix_audit_outbox_retry", "status", "next_attempt_at"),
    )

    @validates("payload")
    def sanitize_outbox_payload(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_json_mapping(value)

    @validates("event_kind")
    def sanitize_event_kind(self, _key: str, value: str) -> str:
        return _sanitize_bounded_text(value, 32) or "unknown"


def _forbid_append_only_orm_mutation(_mapper, _connection, target) -> None:
    """Block ORM UPDATE/DELETE on claimed append-only audit tables (M7)."""
    table = getattr(target, "__tablename__", type(target).__name__)
    raise PermissionError(f"{table} is append-only")


event.listen(ToolExecution, "before_update", _forbid_append_only_orm_mutation)
event.listen(ToolExecution, "before_delete", _forbid_append_only_orm_mutation)
event.listen(LLMExecution, "before_update", _forbid_append_only_orm_mutation)
event.listen(LLMExecution, "before_delete", _forbid_append_only_orm_mutation)
event.listen(AuditEvent, "before_update", _forbid_append_only_orm_mutation)
event.listen(AuditEvent, "before_delete", _forbid_append_only_orm_mutation)
