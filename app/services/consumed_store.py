"""Atomic consumed-token store for single-use approval tokens (H-NEW3 / H6).

Replaces a process-local ``set`` with a store that can survive restarts and
work across workers when Redis is wired at application startup.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Protocol, runtime_checkable

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_default_lock = threading.Lock()


@runtime_checkable
class ConsumedTokenStore(Protocol):
    """Atomic check-and-consume for approval token nonces."""

    async def try_consume(self, key: str, ttl_seconds: int) -> bool:
        """Return True if *key* was consumed successfully (first time).

        If the key was already consumed, return False.  The entry expires
        after *ttl_seconds* to avoid unbounded storage growth.
        """
        ...  # pragma: no cover

    async def is_consumed(self, key: str) -> bool:
        """Return True if *key* is currently recorded as consumed (peek)."""
        ...  # pragma: no cover


class InMemoryConsumedTokenStore:
    """Thread-safe in-memory store with TTL eviction for isolated tests.

    Uses a lock around check-then-add to prevent double-spend even under
    concurrent ``asyncio.to_thread`` usage.  Not safe across processes.
    """

    def __init__(self) -> None:
        self._consumed: dict[str, float] = {}  # key -> expiry timestamp
        self._lock = threading.Lock()

    def _purge_key_if_expired(self, key: str, now: float) -> None:
        existing_expiry = self._consumed.get(key)
        if existing_expiry is not None and existing_expiry <= now:
            del self._consumed[key]

    def is_consumed_sync(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            existing_expiry = self._consumed.get(key)
            if existing_expiry is None:
                return False
            if existing_expiry <= now:
                del self._consumed[key]
                return False
            return True

    def try_consume_sync(self, key: str, ttl_seconds: int) -> bool:
        now = time.time()
        ttl = max(int(ttl_seconds), 1)
        with self._lock:
            if len(self._consumed) > 10_000:
                self._consumed = {k: exp for k, exp in self._consumed.items() if exp > now}

            self._purge_key_if_expired(key, now)
            existing_expiry = self._consumed.get(key)
            if existing_expiry is not None and existing_expiry > now:
                return False

            self._consumed[key] = now + ttl
            return True

    async def is_consumed(self, key: str) -> bool:
        return self.is_consumed_sync(key)

    async def try_consume(self, key: str, ttl_seconds: int) -> bool:
        return self.try_consume_sync(key, ttl_seconds)


class RedisConsumedTokenStore:
    """Redis-backed atomic consume using SET NX EX (H-NEW3).

    ``SET key 1 NX EX ttl`` is atomic: returns True only on the first call
    for a given key; subsequent calls within the TTL return False.
    """

    def __init__(self, redis_client: object, sync_redis_client: object = None) -> None:
        self._redis = redis_client
        self._sync_redis = sync_redis_client
        self._fallback_memory = InMemoryConsumedTokenStore()

    @staticmethod
    def _redis_key(key: str) -> str:
        return f"consumed_approval:{key}"

    def _get_sync_client(self) -> object | None:
        if self._sync_redis is not None:
            return self._sync_redis
        try:
            import redis

            from app.core.config import settings

            self._sync_redis = redis.from_url(
                settings.redis.url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=settings.redis.socket_timeout_seconds,
            )
            return self._sync_redis
        except (RedisError, OSError, TimeoutError, ValueError):
            return None

    def is_consumed_sync(self, key: str) -> bool:
        from app.core.config import Environment, settings

        client = self._get_sync_client()
        if client is not None:
            try:
                result = client.get(self._redis_key(key))  # type: ignore[union-attr]
                return result is not None
            except (RedisError, OSError, TimeoutError) as exc:
                if settings.environment in (Environment.PRODUCTION, Environment.STAGING):
                    logger.error("redis_consumed_store_outage key=%s: %s", key, exc)
                    raise RuntimeError(
                        f"Redis consumed token store is unavailable in {settings.environment.value}."
                    ) from exc
                logger.warning("redis_consumed_store_fallback_dev key=%s: %s", key, exc)
        elif settings.environment in (Environment.PRODUCTION, Environment.STAGING):
            logger.error("redis_consumed_store_client_unavailable key=%s", key)
            raise RuntimeError(
                f"Redis client is unavailable for consumed token store in {settings.environment.value}."
            )
        return self._fallback_memory.is_consumed_sync(key)

    def try_consume_sync(self, key: str, ttl_seconds: int) -> bool:
        from app.core.config import Environment, settings

        client = self._get_sync_client()
        if client is not None:
            try:
                result = client.set(  # type: ignore[union-attr]
                    self._redis_key(key),
                    "1",
                    nx=True,
                    ex=max(int(ttl_seconds), 1),
                )
                return result is True
            except (RedisError, OSError, TimeoutError) as exc:
                if settings.environment in (Environment.PRODUCTION, Environment.STAGING):
                    logger.error("redis_consumed_store_outage key=%s: %s", key, exc)
                    raise RuntimeError(
                        f"Redis consumed token store is unavailable in {settings.environment.value}."
                    ) from exc
                logger.warning("redis_consumed_store_fallback_dev key=%s: %s", key, exc)
        elif settings.environment in (Environment.PRODUCTION, Environment.STAGING):
            logger.error("redis_consumed_store_client_unavailable key=%s", key)
            raise RuntimeError(
                f"Redis client is unavailable for consumed token store in {settings.environment.value}."
            )
        return self._fallback_memory.try_consume_sync(key, ttl_seconds)

    async def is_consumed(self, key: str) -> bool:
        from app.core.config import Environment, settings

        try:
            result = await self._redis.get(self._redis_key(key))  # type: ignore[union-attr]
            return result is not None
        except (RedisError, OSError, TimeoutError) as exc:
            if settings.environment in (Environment.PRODUCTION, Environment.STAGING):
                logger.error("redis_consumed_store_async_outage key=%s: %s", key, exc)
                raise RuntimeError(
                    f"Redis consumed token store is unavailable in {settings.environment.value}."
                ) from exc
            logger.warning("redis_consumed_store_async_fallback_dev key=%s: %s", key, exc)
            return await self._fallback_memory.is_consumed(key)

    async def try_consume(self, key: str, ttl_seconds: int) -> bool:
        from app.core.config import Environment, settings

        try:
            # redis.asyncio.Redis.set returns True if SET NX succeeded, None/False otherwise.
            result = await self._redis.set(  # type: ignore[union-attr]
                self._redis_key(key),
                "1",
                nx=True,
                ex=max(int(ttl_seconds), 1),
            )
            return result is True
        except (RedisError, OSError, TimeoutError) as exc:
            if settings.environment in (Environment.PRODUCTION, Environment.STAGING):
                logger.error("redis_consumed_store_async_outage key=%s: %s", key, exc)
                raise RuntimeError(
                    f"Redis consumed token store is unavailable in {settings.environment.value}."
                ) from exc
            logger.warning("redis_consumed_store_async_fallback_dev key=%s: %s", key, exc)
            return await self._fallback_memory.try_consume(key, ttl_seconds)


_default_store: ConsumedTokenStore = InMemoryConsumedTokenStore()


def configure_default_store(store: ConsumedTokenStore) -> None:
    """Install the process-wide consume store (call from app startup)."""
    global _default_store
    with _default_lock:
        _default_store = store


def get_default_store() -> ConsumedTokenStore:
    """Return the configured consume store (in-memory until startup wires Redis)."""
    return _default_store
