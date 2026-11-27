"""Link a Telegram chat to an account with a short-lived one-time code."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import AppError
from app.infrastructure.db.models import Conversation, TelegramLink
from app.services.platform.time import as_utc

# 32 symbols without look-alikes (no 0/O, 1/I): 8 of them give 40 bits, so a code
# cannot be guessed within its lifetime under the bot's attempt limits.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
_MAX_CODE_DRAWS = 5


def _hash(code: str) -> str:
    return sha256(code.strip().upper().encode("utf-8")).hexdigest()


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


class TelegramLinkService:
    """Issue codes in the web app, redeem them from the chat, look links up by chat.

    The code is shown once, stored only as a hash, expires quickly, and is cleared when
    redeemed. A chat can belong to one account; redeeming moves it to the new account.
    """

    @staticmethod
    async def issue_code(
        session: AsyncSession, user_id: str, *, ttl: timedelta, now: datetime | None = None
    ) -> tuple[str, datetime]:
        """Give the account a fresh code (replacing any earlier one) that no other link holds.

        Raises a retryable :class:`AppError` (503) in the practically impossible case
        that every drawn code is already held by another link.
        """
        for _ in range(_MAX_CODE_DRAWS):
            code = _new_code()
            taken = await session.scalar(
                select(TelegramLink.id).where(TelegramLink.code_hash == _hash(code))
            )
            if taken is None:
                break
        else:
            raise AppError(
                "Chưa tạo được mã liên kết Telegram, vui lòng thử lại.",
                code="LINK_CODE_UNAVAILABLE",
                status_code=503,
            )
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
        """Link ``chat_id`` to the account holding the unexpired ``code``.

        The chat leaves any account it was linked to before; ``None`` if the code is
        unknown or expired.
        """
        current = now or datetime.now(UTC)
        link = await session.scalar(
            select(TelegramLink).where(TelegramLink.code_hash == _hash(code))
        )
        if link is None or link.code_expires_at is None or as_utc(link.code_expires_at) < current:
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
        """Detach the user's chat; False when nothing was linked."""
        link = await TelegramLinkService.by_user(session, user_id)
        if link is None or link.chat_id is None:
            return False
        link.chat_id = None
        await session.flush()
        return True
