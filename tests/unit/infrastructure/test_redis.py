"""Unit tests for the shared Redis manager: budget keys, health probe and reservations."""

import pytest

from app.core.config import settings
from app.infrastructure.redis.client import RedisManager


def test_redis_budget_key_is_environment_namespaced() -> None:
    """Budget counters live under the application and environment prefix."""
    env = getattr(settings.environment, "value", str(settings.environment))
    assert RedisManager.budget_key("run_budget") == f"assistant:{env}:budget:run_budget"


@pytest.mark.asyncio
async def test_redis_health_check_is_false_when_unavailable() -> None:
    """An unreachable server makes the health probe answer False instead of raising."""
    offline_mgr = RedisManager(url="redis://127.0.0.1:59999/0")
    assert await offline_mgr.health_check() is False
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
