"""Telegram linking endpoints for the web app's settings page."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.core.config import settings
from app.infrastructure.db.session import get_db_session
from app.services.telegram.linking import TelegramLinkService

router = APIRouter(prefix="/telegram", tags=["telegram"])


class TelegramStatus(BaseModel):
    """Whether the bot runs on this server and whether the caller's chat is linked."""

    enabled: bool
    linked: bool
    bot_username: str | None = None


class TelegramLinkCode(BaseModel):
    """One-time code to send to the bot as "/link <code>"."""

    code: str
    expires_at: datetime
    bot_username: str | None = None


@router.get("/status", response_model=TelegramStatus)
async def telegram_status(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TelegramStatus:
    link = await TelegramLinkService.by_user(session, user_id)
    return TelegramStatus(
        enabled=bool(settings.telegram.bot_token),
        linked=link is not None and link.chat_id is not None,
        bot_username=settings.telegram.bot_username,
    )


@router.post("/link-code", response_model=TelegramLinkCode)
async def telegram_link_code(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TelegramLinkCode:
    """Issue a fresh code; it is shown once and replaces any earlier one."""
    ttl = timedelta(minutes=settings.telegram.link_code_ttl_minutes)
    code, expires_at = await TelegramLinkService.issue_code(session, user_id, ttl=ttl)
    return TelegramLinkCode(
        code=code, expires_at=expires_at, bot_username=settings.telegram.bot_username
    )


@router.delete("/link", response_model=TelegramStatus)
async def telegram_unlink(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TelegramStatus:
    await TelegramLinkService.unlink(session, user_id)
    return TelegramStatus(
        enabled=bool(settings.telegram.bot_token),
        linked=False,
        bot_username=settings.telegram.bot_username,
    )
