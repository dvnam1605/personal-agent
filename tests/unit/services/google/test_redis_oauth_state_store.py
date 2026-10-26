"""Unit tests for the Redis-backed single-use OAuth state store."""

import json

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
        "user-1", "http://localhost:8000/auth/google/callback", ["s1", "s1 ", "s2"], "binding-1"
    )
    assert record.user_id == "user-1"
    assert record.scopes == ("s1", "s2")
    stored = json.loads(client.data[store._key(record.state)][0])
    assert "binding-1" not in json.dumps(stored)

    consumed = await store.consume(record.state, "binding-1")
    assert consumed.code_verifier == record.code_verifier
    assert consumed.user_id == "user-1"
    # Second consumption must fail: the state was deleted atomically.
    with pytest.raises(AuthenticationError):
        await store.consume(record.state, "binding-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("presented_binding", [None, "", "another-browser"])
async def test_consume_rejects_a_state_without_its_browser_binding(
    presented_binding: str | None,
) -> None:
    """Completing an owner's state from another browser fails, whatever identity it claims."""
    store = _store(FakeAsyncRedis())
    record = await store.issue("owner", "http://redirect", ["scope"], "owner-browser")

    with pytest.raises(AuthenticationError):
        await store.consume(record.state, presented_binding)
    # The rejected attempt still consumed the state, so it cannot be retried.
    with pytest.raises(AuthenticationError):
        await store.consume(record.state, "owner-browser")


@pytest.mark.asyncio
async def test_consume_rejects_unknown_state_and_unbound_legacy_records() -> None:
    client = FakeAsyncRedis()
    store = _store(client)

    with pytest.raises(AuthenticationError):
        await store.consume("missing-state", "binding")

    legacy = {
        "user_id": "owner",
        "code_verifier": "verifier",
        "redirect_uri": "http://redirect",
        "scopes": ["scope"],
        "issued_at": utc_now().isoformat(),
    }
    client.data[store._key("legacy-state")] = (json.dumps(legacy), 600)
    with pytest.raises(AuthenticationError):
        await store.consume("legacy-state", "binding")


@pytest.mark.asyncio
async def test_issue_requires_a_browser_binding() -> None:
    with pytest.raises(ValueError):
        await _store(FakeAsyncRedis()).issue("owner", "http://redirect", ["scope"], "")


@pytest.mark.asyncio
async def test_ttl_is_positive_and_validated() -> None:
    with pytest.raises(ValueError):
        _store(FakeAsyncRedis(), ttl=0)

    client = FakeAsyncRedis()
    store = _store(client, ttl=120)
    record = await store.issue("u", "http://r", ["s"], "b")
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
    record = await store.issue("u", "http://r", ["s"], "b")
    clock.offset_seconds = 0.0
    with pytest.raises(AuthenticationError):
        await store.consume(record.state, "b")


def _factory(client: FakeAsyncRedis):
    async def factory():
        return client

    return factory


def test_default_key_prefix_is_stable() -> None:
    store = _store(FakeAsyncRedis())
    assert store._key("abc") == "assistant:oauth-state:abc"


def _unused_clock() -> Clock:
    return utc_now
