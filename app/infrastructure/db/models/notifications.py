"""Notifications the proactive worker leaves for a user (briefings, meeting dossiers)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base, TimestampMixin
from app.infrastructure.db.models.common import _JSONB


class Notification(Base, TimestampMixin):
    """One message shown in the user's notification centre.

    ``dedup_key`` names the occurrence a job produced it for (``briefing:2026-10-27``,
    ``meeting_prep:<event>:<start>``); it is unique per user, so a job that runs twice —
    after a restart or in a second worker process — never notifies twice.
    """

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(_JSONB, default=dict, nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "dedup_key", name="uq_notifications_user_dedup"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )
