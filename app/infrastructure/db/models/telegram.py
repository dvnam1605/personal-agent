"""Telegram chats linked to accounts."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base, TimestampMixin


class TelegramLink(Base, TimestampMixin):
    """One account's Telegram chat, or the pending one-time code that will link it.

    Only the SHA-256 of the link code is stored. ``conversation_id`` is the conversation
    Telegram messages belong to, so a question asked mid-request can be answered from
    the chat. ``last_notified_at`` marks how far notifications were forwarded.
    """

    __tablename__ = "telegram_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
