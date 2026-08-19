"""Unit tests for Redis manager, environment-namespaced keys, and fallback behavior."""

import pytest

from app.core.config import settings
from app.infrastructure.redis.client import RedisManager


def test_redis_namespaced_keys() -> None:
    """Verify keyspace formatting with application and environment prefixes."""
    env = getattr(settings.environment, "value", str(settings.environment))
    assert RedisManager.session_key("s_123") == f"assistant:{env}:session:s_123"
    assert RedisManager.cache_key("c_456") == f"assistant:{env}:cache:c_456"
    assert (
        RedisManager.ratelimit_key("u_789", "2026-08-17-10-00")
        == f"assistant:{env}:ratelimit:u_789:2026-08-17-10-00"
    )
    assert RedisManager.budget_key("run_budget") == f"assistant:{env}:budget:run_budget"


@pytest.mark.asyncio
async def test_redis_fallback_when_unavailable() -> None:
    """Verify RedisManager gracefully returns None/False without crashing when offline."""
    # Point to non-routable port to ensure offline simulation
    offline_mgr = RedisManager(url="redis://127.0.0.1:59999/0")

    # Health check should return False
    health = await offline_mgr.health_check()
    assert health is False

    # Get session should return None
    session_data = await offline_mgr.get_session("non_existent")
    assert session_data is None

    # Set session should return False
    saved = await offline_mgr.set_session("s_1", {"key": "value"})
    assert saved is False

    # Get cache should return None
    cached = await offline_mgr.get_cache("key_1")
    assert cached is None

    # Set cache should return False
    cache_saved = await offline_mgr.set_cache("key_1", {"data": 123})
    assert cache_saved is False

    # Delete should return False
    deleted = await offline_mgr.delete("key_1")
    assert deleted is False

    await offline_mgr.close()


@pytest.mark.asyncio
async def test_redis_budget_reservation_uses_atomic_ttl_script() -> None:
    """Verify the budget reservation call carries a positive TTL and all resource limits."""

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        async def eval(self, *args: object) -> int:
            self.calls.append(args)
            return 1

    manager = RedisManager(url="redis://unused")
    fake = FakeClient()
    manager._client = fake  # type: ignore[assignment]

    reserved = await manager.reserve(
        "run_lua",
        {"llm_calls": 1, "total_tokens": 10, "cost_micro_usd": 25},
        {"llm_calls": 5, "total_tokens": 100, "cost_micro_usd": 500},
        ttl_seconds=120,
    )

    assert reserved is True
    assert len(fake.calls) == 1
    assert fake.calls[0][1] == 1
    assert fake.calls[0][2] == RedisManager.budget_key("run_lua")
    assert 120 in fake.calls[0][3:]
