"""Link a Telegram chat to an account with a short-lived one-time code."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Conversation, TelegramLink


def _hash(code: str) -> str:
    return sha256(code.strip().encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class TelegramLinkService:
    """Issue codes in the web app, redeem them from the chat, look links up by chat.

    The code is shown once, stored only as a hash, expires quickly, and is cleared when
    redeemed. A chat can belong to one account; redeeming moves it to the new account.
    """

    @staticmethod
    async def issue_code(
        session: AsyncSession, user_id: str, *, ttl: timedelta, now: datetime | None = None
    ) -> tuple[str, datetime]:
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = (now or datetime.now(UTC)) + ttl
        link = await session.scalar(select(TelegramLink).where(TelegramLink.user_id == user_id))
        if link is None:
            link = TelegramLink(user_id=user_id)
            session.add(link)
        link.code_hash = _hash(code)
        link.code_expires_at = expires
        await session.flush()
        return code, expires

    @staticmethod
    async def redeem(
        session: AsyncSession, code: str, chat_id: int, *, now: datetime | None = None
    ) -> TelegramLink | None:
        current = now or datetime.now(UTC)
        link = await session.scalar(
            select(TelegramLink).where(TelegramLink.code_hash == _hash(code))
        )
        if link is None or link.code_expires_at is None or _aware(link.code_expires_at) < current:
            return None
        previous = await session.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
        if previous is not None and previous.id != link.id:
            previous.chat_id = None
            await session.flush()
        if link.conversation_id is None:
            conversation = Conversation(user_id=link.user_id, title="Telegram")
            session.add(conversation)
            await session.flush()
            link.conversation_id = conversation.id
        link.chat_id = chat_id
        link.code_hash = None
        link.code_expires_at = None
        link.linked_at = current
        link.last_notified_at = current
        await session.flush()
        return link

    @staticmethod
    async def by_chat(session: AsyncSession, chat_id: int) -> TelegramLink | None:
        return await session.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))

    @staticmethod
    async def by_user(session: AsyncSession, user_id: str) -> TelegramLink | None:
        return await session.scalar(select(TelegramLink).where(TelegramLink.user_id == user_id))

    @staticmethod
    async def linked(session: AsyncSession) -> list[TelegramLink]:
        rows = await session.scalars(select(TelegramLink).where(TelegramLink.chat_id.is_not(None)))
        return list(rows.all())

    @staticmethod
    async def unlink(session: AsyncSession, user_id: str) -> bool:
        link = await TelegramLinkService.by_user(session, user_id)
        if link is None or link.chat_id is None:
            return False
        link.chat_id = None
        await session.flush()
        return True
