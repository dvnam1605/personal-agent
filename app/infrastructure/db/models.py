"""SQLAlchemy ORM models for the 14 core database tables with forward-compatible schemas."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
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
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.sanitization import sanitize_payload, sanitize_string
from app.infrastructure.db.base import Base, TimestampMixin


def _sanitize_json_mapping(value: Any) -> dict[str, Any]:
    """Keep JSON metadata bounded even when models are created outside a service."""
    sanitized = sanitize_payload(
        value if isinstance(value, dict) else {},
        max_string_len=1000,
        max_depth=8,
        max_items=100,
        max_payload_bytes=32_768,
    )
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_bounded_text(value: str | None, max_len: int) -> str | None:
    """Sanitize a bounded SQL string without exceeding its column width."""
    return None if value is None else sanitize_string(value, max_len)[:max_len]


class User(Base, TimestampMixin):
    """User account entity."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    runs: Mapped[list["AssistantRun"]] = relationship("AssistantRun", back_populates="user")
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation", back_populates="user"
    )
    entities: Mapped[list["Entity"]] = relationship("Entity", back_populates="user")
    memories: Mapped[list["Memory"]] = relationship("Memory", back_populates="user")
    documents: Mapped[list["Document"]] = relationship("Document", back_populates="user")
    google_integration: Mapped["GoogleIntegration | None"] = relationship(
        "GoogleIntegration",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class GoogleIntegration(Base, TimestampMixin):
    """Encrypted Google OAuth connection owned by one local user."""

    __tablename__ = "google_integrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    google_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str] = mapped_column(String(32), default="Bearer", nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="google_integration")

    __table_args__ = (Index("ix_google_integrations_user", "user_id", unique=True),)


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


class Entity(Base, TimestampMixin):
    """Resolved entity (people, dates, files)."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="entities")

    __table_args__ = (
        Index("ix_entities_user_type", "user_id", "entity_type"),
        Index("ix_entities_name", "canonical_name"),
    )


class Memory(Base, TimestampMixin):
    """User memory item with semantic embedding and model metadata."""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(Vector(1536), nullable=True)
    embedding_model: Mapped[str] = mapped_column(
        String(64), default="text-embedding-3-large", nullable=False
    )
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=1536, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship("User", back_populates="memories")


class Document(Base, TimestampMixin):
    """Immutable source-document version metadata for the P9 ingestion model."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    logical_document_id: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid.uuid4()), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("logical_document_id", "version_number", name="uq_documents_version"),
        Index("ix_documents_logical_active", "logical_document_id", "is_active"),
        Index(
            "uq_documents_one_active_version",
            "logical_document_id",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'active', 'failed', 'archived')",
            name="ck_documents_status",
        ),
        CheckConstraint("version_number > 0", name="ck_documents_version_positive"),
    )

    @validates("metadata_")
    def sanitize_metadata(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_json_mapping(value)


class DocumentChunk(Base):
    """P9-compatible parent/child chunk with source and embedding provenance."""

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    hierarchy_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), default="chunk", nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_chunks.id"), nullable=True, index=True
    )
    heading_path: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    content_raw: Mapped[str] = mapped_column(Text, nullable=False)
    content_embedding_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_block_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    parent_chunker_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    child_chunker_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding: Mapped[Any | None] = mapped_column(Vector(1536), nullable=True)
    embedding_model: Mapped[str] = mapped_column(
        String(64), default="text-embedding-3-large", nullable=False
    )
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=1536, nullable=False)
    provenance_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    document: Mapped["Document"] = relationship("Document", back_populates="chunks")
    parent: Mapped["DocumentChunk | None"] = relationship(
        "DocumentChunk", remote_side="DocumentChunk.id", back_populates="children"
    )
    children: Mapped[list["DocumentChunk"]] = relationship("DocumentChunk", back_populates="parent")

    __table_args__ = (Index("ix_chunks_doc_idx", "document_id", "chunk_index"),)

    @validates("metadata_")
    def sanitize_metadata(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_json_mapping(value)


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
            "status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled')",
            name="ck_approval_requests_status",
        ),
        CheckConstraint(
            "(status = 'pending' AND approved IS NULL) "
            "OR (status = 'approved' AND approved = true) "
            "OR (status IN ('rejected', 'expired', 'cancelled') AND approved = false)",
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
        return _sanitize_json_mapping(value)

    @validates("description", "target", "reason")
    def sanitize_text(self, _key: str, value: str | None) -> str | None:
        return None if value is None else sanitize_string(value, max_string_len=2_000)

    @validates("action_type", "tool_name", "risk_level")
    def sanitize_bounded_text(self, _key: str, value: str | None) -> str | None:
        if value is None:
            return None
        max_len = 32 if _key == "risk_level" else 64
        return sanitize_string(value, max_string_len=max_len)[:max_len]


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


class Skill(Base, TimestampMixin):
    """Registered dynamic skill definition with P14-aligned versioning."""

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(16), default="1.0.0", nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


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
