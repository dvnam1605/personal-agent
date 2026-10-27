"""Notification centre endpoints: list the caller's notifications and mark them read."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.domain.errors import NotFoundError
from app.infrastructure.db.session import get_db_session
from app.services.proactive import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationItem(BaseModel):
    """One notification as shown in the web app."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    title: str
    body: str
    data: dict[str, Any]
    created_at: datetime
    read_at: datetime | None = None


class NotificationListResponse(BaseModel):
    """Newest notifications and the caller's unread total."""

    items: list[NotificationItem]
    unread_count: int


class NotificationReadResponse(BaseModel):
    """Result of marking notifications read."""

    updated: int


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> NotificationListResponse:
    """Return the caller's newest notifications."""
    items = await NotificationService.list_for_user(
        session, user_id, unread_only=unread_only, limit=limit
    )
    return NotificationListResponse(
        items=[NotificationItem.model_validate(item) for item in items],
        unread_count=await NotificationService.unread_count(session, user_id),
    )


@router.post("/read-all", response_model=NotificationReadResponse)
async def mark_all_notifications_read(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> NotificationReadResponse:
    """Mark every unread notification of the caller read."""
    updated = await NotificationService.mark_all_read(session, user_id)
    return NotificationReadResponse(updated=updated)


@router.post("/{notification_id}/read", response_model=NotificationReadResponse)
async def mark_notification_read(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> NotificationReadResponse:
    """Mark one of the caller's notifications read; another user's id is reported as missing."""
    if not await NotificationService.mark_read(session, user_id, notification_id):
        raise NotFoundError("Notification not found.")
    return NotificationReadResponse(updated=1)
