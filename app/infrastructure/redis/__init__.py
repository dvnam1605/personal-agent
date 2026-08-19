"""Redis infrastructure package exporting RedisManager."""

from app.infrastructure.redis.client import RedisManager, redis_manager

__all__ = ["RedisManager", "redis_manager"]
