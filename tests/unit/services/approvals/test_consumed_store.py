"""Consumed-token store: peek vs consume and Redis SET NX fail-closed (H6/L2)."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.approvals.consumed_store import (
    InMemoryConsumedTokenStore,
    RedisConsumedTokenStore,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.set_return: Any = True

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> Any:
        del ex
        if self.set_return is not True:
            return self.set_return
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


@pytest.mark.asyncio
async def test_in_memory_peek_does_not_consume() -> None:
    store = InMemoryConsumedTokenStore()
    assert await store.is_consumed("k1") is False
    assert await store.try_consume("k1", 30) is True
    assert await store.is_consumed("k1") is True
    assert await store.try_consume("k1", 30) is False


@pytest.mark.asyncio
async def test_redis_set_false_is_fail_closed() -> None:
    redis = _FakeRedis()
    store = RedisConsumedTokenStore(redis)
    assert await store.try_consume("k1", 30) is True
    assert await store.is_consumed("k1") is True

    redis.set_return = False
    redis.data.clear()
    assert await store.try_consume("k2", 30) is False
    assert await store.is_consumed("k2") is False
