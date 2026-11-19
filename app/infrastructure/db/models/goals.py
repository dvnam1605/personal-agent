"""Long-running work the user hands the assistant: goals with steps, and watch rules."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base, TimestampMixin
from app.infrastructure.db.models.common import _JSONB


class Goal(Base, TimestampMixin):
    """A goal that spans days or weeks, broken into dated steps.

    ``steps`` is an ordered list of ``{"title", "due", "done"}``; the proactive worker
    reminds the user of due steps and the chat reports progress and marks steps done.
    """

    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    due: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(_JSONB, default=list, nullable=False)

    __table_args__ = (Index("ix_goals_user_status", "user_id", "status"),)


class WatchRule(Base, TimestampMixin):
    """A condition the assistant watches for and reports, never acts on.

    ``kind`` is ``mail_from`` (a sender writes), ``mail_keyword`` (a mail mentions a
    phrase) or ``document_topic`` (a new internal document about a topic is ingested).
    """

    __tablename__ = "watch_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_watch_rules_user_active", "user_id", "active"),)
