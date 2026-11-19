"""Unit tests for live notification delivery: the bus, the SSE stream and worker publishing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes import notifications as notifications_route
from app.core.settings.proactive import ProactiveSettings
from app.infrastructure.db.models import GoogleIntegration, Notification
from app.services.proactive import (
    InMemoryNotificationBus,
    NotificationService,
    ProactiveWorker,
    RedisNotificationBus,
)


class FakeRequest:
    """Reports a disconnect after ``polls`` checks."""

    def __init__(self, polls: int) -> None:
        self.polls = polls

    async def is_disconnected(self) -> bool:
        self.polls -= 1
        return self.polls < 0


def fake_request(polls: int) -> Request:
    return cast(Request, FakeRequest(polls))


async def _collect(response: Any) -> list[str]:
    return [chunk async for chunk in response.body_iterator]


async def test_in_memory_bus_delivers_only_to_the_owner() -> None:
    bus = InMemoryNotificationBus()
    async with bus.subscribe("user_a") as mine, bus.subscribe("user_b") as theirs:
        await bus.publish("user_a", "n1")
        assert await asyncio.wait_for(mine.get(), 1) == "n1"
        assert theirs.empty()
    await bus.publish("user_a", "n2")


async def test_stream_sends_ready_then_new_ids(monkeypatch: Any) -> None:
    bus = InMemoryNotificationBus()
    monkeypatch.setattr(notifications_route, "HEARTBEAT_SECONDS", 0.05)
    response = await notifications_route.stream_notifications(
        fake_request(3), user_id="user_a", bus=bus
    )

    async def publish_soon() -> None:
        await asyncio.sleep(0.01)
        await bus.publish("user_a", "n1")

    publisher = asyncio.create_task(publish_soon())
    chunks = await asyncio.wait_for(_collect(response), timeout=2)
    await publisher

    assert chunks[0] == "event: ready\ndata: {}\n\n"
    assert 'event: notification\ndata: {"id": "n1"}\n\n' in chunks
    assert response.media_type == "text/event-stream"


async def test_stream_reports_an_unavailable_bus() -> None:
    async def broken_client() -> Any:
        raise OSError("redis down")

    response = await notifications_route.stream_notifications(
        fake_request(1), user_id="user_a", bus=RedisNotificationBus(broken_client)
    )
    assert await _collect(response) == ["event: unavailable\ndata: {}\n\n"]


async def test_redis_bus_publish_failure_is_swallowed() -> None:
    async def broken_client() -> Any:
        raise OSError("redis down")

    await RedisNotificationBus(broken_client).publish("user_a", "n1")


async def test_redis_bus_round_trip_over_a_fake_pubsub() -> None:
    class FakePubSub:
        def __init__(self) -> None:
            self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            self.closed = False

        async def subscribe(self, channel: str) -> None:
            self.channel = channel

        async def unsubscribe(self, channel: str) -> None:
            return None

        async def aclose(self) -> None:
            self.closed = True

        async def listen(self) -> Any:
            while True:
                yield await self.queue.get()

    pubsub = FakePubSub()

    class FakeClient:
        def pubsub(self) -> FakePubSub:
            return pubsub

        async def publish(self, channel: str, data: str) -> None:
            await pubsub.queue.put({"type": "subscribe", "data": 1})
            await pubsub.queue.put({"type": "message", "data": "not json"})
            await pubsub.queue.put({"type": "message", "data": data})

    async def client() -> FakeClient:
        return FakeClient()

    bus = RedisNotificationBus(client)
    async with bus.subscribe("user_a") as queue:
        await bus.publish("user_a", "n9")
        assert await asyncio.wait_for(queue.get(), 1) == "n9"
        assert pubsub.channel.endswith(":notifications:user_a")
    assert pubsub.closed


async def test_worker_publishes_committed_notifications(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(GoogleIntegration(user_id="user_a", refresh_token_encrypted="enc"))
        await session.commit()

    class OneShot:
        name = "one_shot"

        async def run(
            self, session: AsyncSession, user_id: str, now: datetime
        ) -> list[Notification]:
            created = await NotificationService.create(
                session, user_id=user_id, kind="t", title="t", body="b", dedup_key="once"
            )
            return [created] if created is not None else []

    bus = InMemoryNotificationBus()
    now = datetime(2026, 10, 27, tzinfo=UTC)
    worker = ProactiveWorker(
        session_factory, [OneShot()], ProactiveSettings(), clock=lambda: now, bus=bus
    )
    async with bus.subscribe("user_a") as queue:
        assert await worker.run_once() == 1
        notification_id = await asyncio.wait_for(queue.get(), 1)
    async with session_factory() as session:
        assert await session.get(Notification, notification_id) is not None
