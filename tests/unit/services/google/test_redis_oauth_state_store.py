"""Unit tests for the Redis-backed single-use OAuth state store."""

import pytest

from app.domain.errors import AuthenticationError
from app.services.google.auth import Clock, RedisOAuthStateStore, utc_now


class FakeAsyncRedis:
    """Minimal async Redis surface: set with TTL + atomic getdel."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[str, int]] = {}

    async def set(self, key: str, value: str, ex: int) -> None:
        self.data[key] = (value, ex)

    async def getdel(self, key: str):
        item = self.data.pop(key, None)
        return item[0] if item else None


def _store(client: FakeAsyncRedis, ttl: int = 600) -> RedisOAuthStateStore:
    async def factory():
        return client

    return RedisOAuthStateStore(factory, ttl_seconds=ttl)


@pytest.mark.asyncio
async def test_issue_and_consume_roundtrip_is_single_use() -> None:
    client = FakeAsyncRedis()
    store = _store(client)

    record = await store.issue(
        "user-1", "http://localhost:8000/auth/google/callback", ["s1", "s1 ", "s2"]
    )
    assert record.user_id == "user-1"
    assert record.scopes == ("s1", "s2")

    consumed = await store.consume(record.state, "user-1")
    assert consumed.code_verifier == record.code_verifier
    # Second consumption must fail: the state was deleted atomically.
    with pytest.raises(AuthenticationError):
        await store.consume(record.state, "user-1")


@pytest.mark.asyncio
async def test_consume_rejects_unknown_or_mismatched_state() -> None:
    store = _store(FakeAsyncRedis())

    with pytest.raises(AuthenticationError):
        await store.consume("missing-state", "user-1")

    client = FakeAsyncRedis()
    store2 = _store(client)
    record = await store2.issue("owner", "http://redirect", ["scope"])
    with pytest.raises(AuthenticationError):
        await store2.consume(record.state, "attacker")


@pytest.mark.asyncio
async def test_ttl_is_positive_and_validated() -> None:
    with pytest.raises(ValueError):
        _store(FakeAsyncRedis(), ttl=0)

    client = FakeAsyncRedis()
    store = _store(client, ttl=120)
    record = await store.issue("u", "http://r", ["s"])
    _, stored_ttl = client.data[store._key(record.state)]
    assert stored_ttl == 120


@pytest.mark.asyncio
async def test_expired_state_is_rejected_by_clock() -> None:
    class BackdatingClock:
        def __init__(self) -> None:
            self.offset_seconds = 0.0

        def __call__(self):
            from datetime import UTC, datetime, timedelta

            return datetime.now(UTC) - timedelta(seconds=self.offset_seconds)

    clock = BackdatingClock()
    client = FakeAsyncRedis()
    store = RedisOAuthStateStore(
        _factory(client),
        ttl_seconds=60,
        clock=clock,  # type: ignore[arg-type]
    )
    clock.offset_seconds = 3600.0
    record = await store.issue("u", "http://r", ["s"])
    clock.offset_seconds = 0.0
    with pytest.raises(AuthenticationError):
        await store.consume(record.state, "u")


def _factory(client: FakeAsyncRedis):
    async def factory():
        return client

    return factory


def test_default_key_prefix_is_stable() -> None:
    store = _store(FakeAsyncRedis())
    assert store._key("abc") == "assistant:oauth-state:abc"


def _unused_clock() -> Clock:
    return utc_now
