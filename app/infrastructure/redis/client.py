"""Shared async Redis client: lazy connection, health probe and atomic run-budget reservations.

Callers that need other Redis structures (rate limits, single-use tokens, the
notification bus) take the client from ``get_client`` and namespace their own keys.
"""

from collections.abc import Mapping

import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError

from app.core.config import settings

logger = structlog.get_logger(__name__)

_REDIS_IO_ERRORS = (RedisError, OSError, TimeoutError, ValueError)


class RedisManager:
    """Owns the process-wide Redis client and the run-budget reservation script."""

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
        except _REDIS_IO_ERRORS as e:
            logger.warning("redis_health_check_failed", error=str(e))
            self._is_connected = False
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

    async def close(self) -> None:
        """Close underlying connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


redis_manager = RedisManager()
