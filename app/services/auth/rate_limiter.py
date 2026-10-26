"""Authentication rate limiter and account lockout protection with Redis and in-memory fallback."""

import time
from collections import defaultdict
from dataclasses import dataclass

import structlog

from app.core.config import settings
from app.infrastructure.redis.client import redis_manager

logger = structlog.get_logger(__name__)

# Security parameters
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 15 * 60  # 15 minutes
MAX_IP_LOGIN_PER_MINUTE = 20
MAX_IP_REGISTER_PER_HOUR = 10


@dataclass
class _InMemoryBucket:
    count: int = 0
    expires_at: float = 0.0


class AuthRateLimiter:
    """Provides IP rate limiting and account lockout for authentication endpoints."""

    def __init__(self) -> None:
        self._memory_store: dict[str, _InMemoryBucket] = defaultdict(_InMemoryBucket)

    @staticmethod
    def _get_key_prefix() -> str:
        env = getattr(settings.environment, "value", str(settings.environment))
        return f"assistant:{env}:auth_ratelimit"

    def _clean_memory_store(self) -> None:
        now = time.time()
        expired_keys = [k for k, v in self._memory_store.items() if v.expires_at <= now]
        for k in expired_keys:
            del self._memory_store[k]

    async def check_login_allowed(self, email: str, client_ip: str) -> tuple[bool, str | None, int]:
        """Admit one login attempt for ``email`` from ``client_ip``, or refuse it.

        The per-IP throttle is checked first. The account attempt is then reserved
        atomically before the password is verified (INCR in Redis; a single step on the
        event loop for the memory fallback), so concurrent requests can never evaluate
        more than MAX_FAILED_LOGIN_ATTEMPTS guesses per lockout window. A successful
        login clears the counter.

        Returns:
            (is_allowed, error_message, retry_after_seconds)
        """
        throttled = await self._check_ip_login(client_ip)
        if throttled is not None:
            return throttled

        clean_email = email.strip().lower()
        failed_key = f"{self._get_key_prefix()}:failed:{clean_email}"
        attempts, ttl = await self._reserve_login_attempt(failed_key)
        if attempts > MAX_FAILED_LOGIN_ATTEMPTS:
            logger.warning(
                "auth.lockout_blocked",
                email=clean_email,
                client_ip=client_ip,
                failed_count=attempts,
                ttl=ttl,
            )
            mins = max(1, round(ttl / 60))
            return (
                False,
                f"Tài khoản đang bị tạm khóa do nhập sai quá {MAX_FAILED_LOGIN_ATTEMPTS} lần liên tiếp. "
                f"Vui lòng thử lại sau {mins} phút.",
                ttl,
            )
        return True, None, 0

    async def _reserve_login_attempt(self, failed_key: str) -> tuple[int, int]:
        """Count one more attempt in the lockout window; return (attempts, seconds left)."""
        try:
            client = await redis_manager.get_client()
            attempts = int(await client.incr(failed_key))
            ttl = int(await client.ttl(failed_key))
            if attempts == 1 or ttl < 0:
                await client.expire(failed_key, LOCKOUT_DURATION_SECONDS)
                ttl = LOCKOUT_DURATION_SECONDS
            return attempts, max(1, ttl)
        except Exception as exc:  # noqa: BLE001 - any Redis failure falls back to memory
            logger.warning(
                "auth.ratelimit_redis_fallback", action="reserve_attempt", error=str(exc)
            )
            now = time.time()
            self._clean_memory_store()
            bucket = self._memory_store[failed_key]
            if bucket.expires_at <= now:
                bucket.count = 0
                bucket.expires_at = now + LOCKOUT_DURATION_SECONDS
            bucket.count += 1
            return bucket.count, max(1, int(bucket.expires_at - now))

    async def _check_ip_login(self, client_ip: str) -> tuple[bool, str | None, int] | None:
        """Per-IP login throttle; returns the refusal, or None when the attempt may proceed."""
        now = time.time()
        ip_key = f"{self._get_key_prefix()}:ip_login:{client_ip}"
        try:
            client = await redis_manager.get_client()
            ip_count_raw = await client.get(ip_key)
            ip_count = int(ip_count_raw) if ip_count_raw else 0
            if ip_count >= MAX_IP_LOGIN_PER_MINUTE:
                ttl = await client.ttl(ip_key)
                ttl = max(1, ttl) if ttl > 0 else 60
                logger.warning("auth.ip_login_throttled", client_ip=client_ip, ip_count=ip_count)
                return (
                    False,
                    "Quá nhiều lượt thử đăng nhập từ địa chỉ mạng của bạn. Vui lòng thử lại sau.",
                    ttl,
                )
            pipe = client.pipeline()
            pipe.incr(ip_key)
            pipe.expire(ip_key, 60)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - any Redis failure falls back to memory
            logger.warning("auth.ratelimit_redis_fallback", action="check_ip_login", error=str(exc))
            self._clean_memory_store()
            bucket = self._memory_store[ip_key]
            if bucket.expires_at <= now:
                bucket.count = 1
                bucket.expires_at = now + 60
            else:
                bucket.count += 1
                if bucket.count > MAX_IP_LOGIN_PER_MINUTE:
                    ttl = max(1, int(bucket.expires_at - now))
                    return (
                        False,
                        "Quá nhiều lượt thử đăng nhập từ địa chỉ mạng của bạn. Vui lòng thử lại sau.",
                        ttl,
                    )
        return None

    async def login_failure_count(self, email: str) -> int:
        """Attempts counted in the current lockout window (each was reserved before verifying)."""
        clean_email = email.strip().lower()
        failed_key = f"{self._get_key_prefix()}:failed:{clean_email}"
        try:
            client = await redis_manager.get_client()
            raw = await client.get(failed_key)
            return int(raw) if raw else 0
        except Exception as exc:  # noqa: BLE001 - any Redis failure falls back to memory
            logger.warning("auth.ratelimit_redis_fallback", action="failure_count", error=str(exc))
            bucket = self._memory_store.get(failed_key)
            return bucket.count if bucket and bucket.expires_at > time.time() else 0

    async def clear_login_failures(self, email: str) -> None:
        """Reset consecutive failed login counter upon successful login."""
        clean_email = email.strip().lower()
        failed_key = f"{self._get_key_prefix()}:failed:{clean_email}"
        try:
            client = await redis_manager.get_client()
            await client.delete(failed_key)
        except Exception as exc:  # noqa: BLE001 - any Redis failure falls back to memory
            logger.warning("auth.ratelimit_redis_fallback", action="clear_failures", error=str(exc))
            if failed_key in self._memory_store:
                del self._memory_store[failed_key]

    async def check_register_allowed(self, client_ip: str) -> tuple[bool, str | None, int]:
        """Check if registration is allowed from the given client IP.

        Returns:
            (is_allowed, error_message, retry_after_seconds)
        """
        now = time.time()
        ip_key = f"{self._get_key_prefix()}:ip_register:{client_ip}"

        try:
            client = await redis_manager.get_client()
            reg_count_raw = await client.get(ip_key)
            reg_count = int(reg_count_raw) if reg_count_raw else 0
            if reg_count >= MAX_IP_REGISTER_PER_HOUR:
                ttl = await client.ttl(ip_key)
                ttl = max(1, ttl) if ttl > 0 else 3600
                logger.warning(
                    "auth.ip_register_throttled", client_ip=client_ip, reg_count=reg_count
                )
                return (
                    False,
                    "Bạn đã đăng ký quá nhiều tài khoản từ địa chỉ mạng này. Vui lòng thử lại sau 1 giờ.",
                    ttl,
                )
            pipe = client.pipeline()
            pipe.incr(ip_key)
            pipe.expire(ip_key, 3600)
            await pipe.execute()
            return True, None, 0
        except Exception as exc:  # noqa: BLE001 - any Redis failure falls back to memory
            logger.warning("auth.ratelimit_redis_fallback", action="check_register", error=str(exc))
            self._clean_memory_store()
            bucket = self._memory_store[ip_key]
            if bucket.expires_at <= now:
                bucket.count = 1
                bucket.expires_at = now + 3600
            else:
                bucket.count += 1
                if bucket.count > MAX_IP_REGISTER_PER_HOUR:
                    ttl = max(1, int(bucket.expires_at - now))
                    return (
                        False,
                        "Bạn đã đăng ký quá nhiều tài khoản từ địa chỉ mạng này. Vui lòng thử lại sau 1 giờ.",
                        ttl,
                    )
            return True, None, 0


# Global singleton rate limiter instance
auth_rate_limiter = AuthRateLimiter()
