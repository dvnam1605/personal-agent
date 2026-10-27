"""Live delivery of new notifications to open browser tabs (Redis pub/sub, in-memory fallback)."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol

import structlog

from app.core.config import Environment, settings
from app.infrastructure.redis.client import redis_manager

logger = structlog.get_logger(__name__)


def channel_name(user_id: str) -> str:
    """Per-user, environment-namespaced pub/sub channel."""
    env = getattr(settings.environment, "value", str(settings.environment))
    return f"assistant:{env}:notifications:{user_id}"


class NotificationBus(Protocol):
    """Announces that a user has a new notification; the payload carries only its id.

    Subscribers re-read the notification through the owner-bound API, so nothing on the
    bus needs to be trusted and a lost message only delays the update until the next poll.
    """

    async def publish(self, user_id: str, notification_id: str) -> None: ...

    def subscribe(self, user_id: str) -> Any: ...


class InMemoryNotificationBus:
    """Process-local bus for tests and for running without Redis."""

    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)

    async def publish(self, user_id: str, notification_id: str) -> None:
        for queue in list(self._queues.get(user_id, ())):
            queue.put_nowait(notification_id)

    @asynccontextmanager
    async def subscribe(self, user_id: str) -> AsyncIterator[asyncio.Queue[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._queues[user_id].add(queue)
        try:
            yield queue
        finally:
            self._queues[user_id].discard(queue)


class RedisNotificationBus:
    """Bus over Redis pub/sub so the worker and every web worker process share events.

    Each subscription feeds an asyncio queue from a reader task; publishing failures are
    logged and swallowed because the notification itself is already stored.
    """

    def __init__(self, client_factory: Callable[[], Awaitable[Any]]) -> None:
        self._client_factory = client_factory

    async def publish(self, user_id: str, notification_id: str) -> None:
        try:
            client = await self._client_factory()
            await client.publish(channel_name(user_id), json.dumps({"id": notification_id}))
        except Exception as exc:  # noqa: BLE001 - live push is best effort; polling covers it
            logger.warning("notification_publish_failed", error=str(exc))

    @asynccontextmanager
    async def subscribe(self, user_id: str) -> AsyncIterator[asyncio.Queue[str]]:
        client = await self._client_factory()
        pubsub = client.pubsub()
        await pubsub.subscribe(channel_name(user_id))
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def _pump() -> None:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    queue.put_nowait(str(json.loads(message["data"])["id"]))
                except (KeyError, TypeError, ValueError):
                    continue

        reader = asyncio.create_task(_pump(), name="notification-subscriber")
        try:
            yield queue
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            await pubsub.unsubscribe(channel_name(user_id))
            await pubsub.aclose()


_bus: NotificationBus | None = None


def get_notification_bus() -> NotificationBus:
    """Process-wide bus: Redis outside tests, in-memory under TESTING."""
    global _bus
    if _bus is None:
        if settings.environment is Environment.TESTING:
            _bus = InMemoryNotificationBus()
        else:
            _bus = RedisNotificationBus(redis_manager.get_client)
    return _bus


def set_notification_bus(bus: NotificationBus | None) -> None:
    """Replace the process-wide bus (tests)."""
    global _bus
    _bus = bus
