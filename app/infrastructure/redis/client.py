"""Async Redis client manager with environment-namespaced keys, TTLs, and graceful degradation."""

import json
from collections.abc import Mapping
from typing import Any

import redis.asyncio as aioredis
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


class RedisManager:
    """Manages environment-namespaced Redis caching, session states, and rate limiting with fallback."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or settings.redis.url
        self._client: aioredis.Redis | None = None
        self._is_connected: bool = False

    async def get_client(self) -> aioredis.Redis:
        """Get or initialize the underlying async Redis client."""
        if self._client is None:
            self._client = aioredis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=settings.redis.socket_timeout_seconds,
            )
        return self._client

    @staticmethod
    def session_key(session_id: str) -> str:
        """Generate environment-namespaced Redis key for session state."""
        env = getattr(settings.environment, "value", str(settings.environment))
        return f"assistant:{env}:session:{session_id}"

    @staticmethod
    def cache_key(key: str) -> str:
        """Generate environment-namespaced Redis key for general cache."""
        env = getattr(settings.environment, "value", str(settings.environment))
        return f"assistant:{env}:cache:{key}"

    @staticmethod
    def ratelimit_key(user_id: str, bucket: str) -> str:
        """Generate environment-namespaced Redis key for rate-limiting bucket."""
        env = getattr(settings.environment, "value", str(settings.environment))
        return f"assistant:{env}:ratelimit:{user_id}:{bucket}"

    @staticmethod
    def budget_key(run_id: str) -> str:
        """Generate a run-scoped key for hard distributed budget reservations."""
        env = getattr(settings.environment, "value", str(settings.environment))
        return f"assistant:{env}:budget:{run_id}"

    async def health_check(self) -> bool:
        """Perform a quick PING health check."""
        try:
            client = await self.get_client()
            res = await client.ping()
            self._is_connected = bool(res)
            return self._is_connected
        except Exception as e:
            logger.warning("redis_health_check_failed", error=str(e))
            self._is_connected = False
            return False

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Fetch versioned session state envelope from Redis."""
        try:
            client = await self.get_client()
            raw = await client.get(self.session_key(session_id))
            if not raw:
                return None
            data = json.loads(raw)
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            return data
        except Exception as e:
            logger.warning("redis_get_session_fallback", session_id=session_id, error=str(e))
            return None

    async def set_session(self, session_id: str, data: dict[str, Any], ttl: int = 86400) -> bool:
        """Store versioned session state envelope with TTL (default 24h)."""
        try:
            client = await self.get_client()
            envelope = {"version": 1, "data": data}
            await client.set(self.session_key(session_id), json.dumps(envelope), ex=ttl)
            return True
        except Exception as e:
            logger.warning("redis_set_session_fallback", session_id=session_id, error=str(e))
            return False

    async def get_cache(self, key: str) -> Any | None:
        """Fetch cached JSON payload (fails open)."""
        try:
            client = await self.get_client()
            raw = await client.get(self.cache_key(key))
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning("redis_get_cache_fallback", key=key, error=str(e))
            return None

    async def set_cache(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Store cached JSON payload with TTL (default 1h)."""
        try:
            client = await self.get_client()
            await client.set(self.cache_key(key), json.dumps(value), ex=ttl)
            return True
        except Exception as e:
            logger.warning("redis_set_cache_fallback", key=key, error=str(e))
            return False

    async def reserve(
        self,
        run_id: str,
        requested: Mapping[str, int],
        limits: Mapping[str, int],
        ttl_seconds: int,
    ) -> bool:
        """Atomically reserve non-refilling run budget capacity across workers.

        The Lua script performs every limit check before incrementing any counter. The key TTL
        is refreshed on every successful reservation (sliding window), so long-running runs
        cannot lose their accumulated counters mid-execution. Redis errors intentionally
        propagate so callers can deny the new call rather than use local state.
        """
        if ttl_seconds <= 0:
            raise ValueError("Budget reservation TTL must be positive.")
        if any(value < 0 for value in requested.values()):
            raise ValueError("Budget reservation values must be non-negative.")

        lua = """
        for i = 2, #ARGV, 3 do
            local field = ARGV[i]
            local requested = tonumber(ARGV[i + 1])
            local limit = tonumber(ARGV[i + 2])
            local current = tonumber(redis.call('HGET', KEYS[1], field) or '0')
            if current + requested > limit then
                return 0
            end
        end
        for i = 2, #ARGV, 3 do
            redis.call('HINCRBY', KEYS[1], ARGV[i], tonumber(ARGV[i + 1]))
        end
        redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
        return 1
        """
        arguments: list[str | int] = [ttl_seconds]
        for resource, amount in requested.items():
            arguments.extend((resource, amount, limits[resource]))
        client = await self.get_client()
        result = await client.eval(lua, 1, self.budget_key(run_id), *arguments)
        return bool(result)

    async def delete(self, key: str) -> bool:
        """Delete a key."""
        try:
            client = await self.get_client()
            await client.delete(key)
            return True
        except Exception as e:
            logger.warning("redis_delete_fallback", key=key, error=str(e))
            return False

    async def close(self) -> None:
        """Close underlying connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


redis_manager = RedisManager()
