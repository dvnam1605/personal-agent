"""Source documents (versions, chunks, profiles, relations), ingestion jobs and generated reports."""

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.infrastructure.db.base import Base, TimestampMixin
from app.infrastructure.db.models.common import (
    _JSONB,
    _sanitize_document_metadata,
)

if TYPE_CHECKING:
    from app.infrastructure.db.models.users import User


class Document(Base, TimestampMixin):
    """Immutable source-document version metadata for the P9 ingestion model."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Nullable since P9D: pipeline-level ingestion has no end-user attribution.
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
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
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", _JSONB, default=dict, nullable=False
    )

    user: Mapped["User | None"] = relationship("User", back_populates="documents")
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
        Index("ix_documents_logical_fingerprint", "logical_document_id", "fingerprint"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'active', 'failed', 'archived')",
            name="ck_documents_status",
        ),
        CheckConstraint("version_number > 0", name="ck_documents_version_positive"),
    )

    @validates("metadata_")
    def sanitize_metadata(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_document_metadata(value)


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
    embedding: Mapped[Any | None] = mapped_column(Vector(1024), nullable=True)
    embedding_model: Mapped[str] = mapped_column(
        String(64), default="AITeamVN/Vietnamese_Embedding", nullable=False
    )
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=1024, nullable=False)
    provenance_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", _JSONB, default=dict, nullable=False
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
        return _sanitize_document_metadata(value)


class DocumentProfile(Base):
    """Category + typed metadata for one document version (all document kinds).

    Owner scope and ``is_active`` are never copied here: queries always join
    ``documents`` so a profile cannot drift from its version's visibility.
    """

    __tablename__ = "document_profiles"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    logical_document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    doc_category: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    doc_subtype: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    doc_number_norm: Mapped[str | None] = mapped_column(String(128), nullable=True)
    issuer: Mapped[str | None] = mapped_column(Text, nullable=True)
    issuer_norm: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    doc_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(_JSONB, default=list, nullable=False)
    entity_names_norm: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(_JSONB, default=list, nullable=False)
    extras: Mapped[dict[str, Any]] = mapped_column(_JSONB, default=dict, nullable=False)
    field_sources: Mapped[dict[str, str]] = mapped_column(_JSONB, default=dict, nullable=False)
    overrides: Mapped[dict[str, Any]] = mapped_column(_JSONB, default=dict, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    category_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_document_profiles_category_date", "doc_category", "doc_date"),
        Index("ix_document_profiles_number", "doc_number_norm"),
        Index("ix_document_profiles_issuer", "issuer_norm"),
        Index("ix_document_profiles_logical", "logical_document_id"),
        Index("ix_document_profiles_input_hash", "input_hash"),
        CheckConstraint(
            "doc_category IN ('administrative', 'report', 'contract', 'minutes', "
            "'technical', 'academic', 'spreadsheet', 'other')",
            name="ck_document_profiles_category",
        ),
    )


class DocumentRelation(Base):
    """One relation stated by a document version (replaces, based on, signed by, awards…).

    Like profiles, owner scope and ``is_active`` live only on ``documents``;
    every query joins it. ``target_norm`` is the unaccented target text, so
    OCR variants of the same name ("Đỗ Tiến Sỹ" / "Dõ Tiến Sỹ") collide.
    """

    __tablename__ = "document_relations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    logical_document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_norm: Mapped[str] = mapped_column(Text, nullable=False)
    target_number_norm: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(_JSONB, default=dict, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        Index("ix_document_relations_document", "document_id"),
        Index("ix_document_relations_type_number", "relation_type", "target_number_norm"),
        Index("ix_document_relations_type_target", "relation_type", "target_norm"),
    )


class IngestionJobRecord(Base):
    """Durable ingestion job with worker-safe claims (spec P9D-3, P3 patterns)."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    logical_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    timings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    parent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    child_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    table_child_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'PARSING', 'BUILDING_PARENTS', "
            "'BUILDING_CHILDREN', 'EMBEDDING', 'PERSISTING', 'COMPLETED', "
            "'FAILED', 'SKIPPED', 'NEEDS_OCR')",
            name="ck_ingestion_jobs_status",
        ),
        Index("ix_ingestion_jobs_status_created", "status", "created_at"),
    )


class GeneratedDocument(Base):
    """A document written on request: outline → user approval → sections → files."""

    __tablename__ = "generated_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[str] = mapped_column(Text, nullable=False)
    outline: Mapped[dict[str, Any]] = mapped_column(_JSONB, default=dict, nullable=False)
    source_document_ids: Mapped[list[str]] = mapped_column(_JSONB, default=list, nullable=False)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(_JSONB, default=list, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(_JSONB, default=list, nullable=False)
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    docx_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="outline", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_generated_documents_user_status", "user_id", "status"),
        Index("ix_generated_documents_conversation", "conversation_id"),
        CheckConstraint(
            "status IN ('outline', 'writing', 'done', 'failed', 'cancelled')",
            name="ck_generated_documents_status",
        ),
    )
