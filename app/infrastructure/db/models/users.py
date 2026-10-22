"""Accounts and what belongs to a user directly: Google link, entities, memories, skills."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.infrastructure.db.models.conversations import AssistantRun, Conversation
    from app.infrastructure.db.models.documents import Document


class User(Base, TimestampMixin):
    """User account entity."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
    embedding: Mapped[Any | None] = mapped_column(Vector(1024), nullable=True)
    embedding_model: Mapped[str] = mapped_column(
        String(64), default="AITeamVN/Vietnamese_Embedding", nullable=False
    )
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=1024, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship("User", back_populates="memories")


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
