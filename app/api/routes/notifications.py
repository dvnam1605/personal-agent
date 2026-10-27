"""Notification centre endpoints: list, mark read, and a live stream of new notifications."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.domain.errors import NotFoundError
from app.infrastructure.db.session import get_db_session
from app.services.proactive import NotificationService
from app.services.proactive.events import NotificationBus, get_notification_bus

router = APIRouter(prefix="/notifications", tags=["notifications"])
logger = structlog.get_logger(__name__)

# Below nginx's 300 s proxy_read_timeout, so an idle stream is never cut by the proxy.
HEARTBEAT_SECONDS = 25.0


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


def get_bus() -> NotificationBus:
    """Dependency seam for the live notification bus."""
    return get_notification_bus()


@router.get("/stream")
async def stream_notifications(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    bus: NotificationBus = Depends(get_bus),  # noqa: B008
) -> StreamingResponse:
    """Server-sent events: ``notification`` with the new id whenever one is stored.

    Only ids are sent; the client re-reads them through the owner-bound list endpoint.
    When the bus is unavailable the stream says so and ends, and the client keeps polling.
    """

    async def events() -> AsyncGenerator[str, None]:
        try:
            async with bus.subscribe(user_id) as queue:
                yield "event: ready\ndata: {}\n\n"
                while not await request.is_disconnected():
                    try:
                        notification_id = await asyncio.wait_for(
                            queue.get(), timeout=HEARTBEAT_SECONDS
                        )
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    payload = json.dumps({"id": notification_id})
                    yield f"event: notification\ndata: {payload}\n\n"
        except (RedisError, OSError) as exc:
            logger.warning("notification_stream_unavailable", error=str(exc))
            yield "event: unavailable\ndata: {}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
