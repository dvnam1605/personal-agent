"""Authentication rate limiter and account lockout protection with Redis and in-memory fallback."""

import time
from collections import defaultdict
from dataclasses import dataclass, field
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
        """Check if login attempt is allowed for the given email and client IP.

        Returns:
            (is_allowed, error_message, retry_after_seconds)
        """
        clean_email = email.strip().lower()
        now = time.time()

        # 1. Check Account Lockout
        failed_key = f"{self._get_key_prefix()}:failed:{clean_email}"
        try:
            client = await redis_manager.get_client()
            failed_count_raw = await client.get(failed_key)
            failed_count = int(failed_count_raw) if failed_count_raw else 0
            if failed_count >= MAX_FAILED_LOGIN_ATTEMPTS:
                ttl = await client.ttl(failed_key)
                ttl = max(1, ttl) if ttl > 0 else LOCKOUT_DURATION_SECONDS
                mins = max(1, round(ttl / 60))
                logger.warning(
                    "auth.lockout_blocked",
                    email=clean_email,
                    client_ip=client_ip,
                    failed_count=failed_count,
                    ttl=ttl,
                )
                return (
                    False,
                    f"Tài khoản đang bị tạm khóa do nhập sai quá {MAX_FAILED_LOGIN_ATTEMPTS} lần liên tiếp. "
                    f"Vui lòng thử lại sau {mins} phút.",
                    ttl,
                )
        except Exception as exc:
            logger.warning("auth.ratelimit_redis_fallback", action="check_lockout", error=str(exc))
            # Fallback to memory
            self._clean_memory_store()
            bucket = self._memory_store.get(failed_key)
            if bucket and bucket.expires_at > now and bucket.count >= MAX_FAILED_LOGIN_ATTEMPTS:
                ttl = max(1, int(bucket.expires_at - now))
                mins = max(1, round(ttl / 60))
                return (
                    False,
                    f"Tài khoản đang bị tạm khóa do nhập sai quá {MAX_FAILED_LOGIN_ATTEMPTS} lần liên tiếp. "
                    f"Vui lòng thử lại sau {mins} phút.",
                    ttl,
                )

        # 2. Check IP Throttling for Login
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
            # Increment IP counter
            pipe = client.pipeline()
            pipe.incr(ip_key)
            pipe.expire(ip_key, 60)
            await pipe.execute()
        except Exception as exc:
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

        return True, None, 0

    async def record_login_failure(self, email: str, client_ip: str) -> int:
        """Record a failed login attempt for the given email and return total consecutive failures."""
        clean_email = email.strip().lower()
        now = time.time()
        failed_key = f"{self._get_key_prefix()}:failed:{clean_email}"

        try:
            client = await redis_manager.get_client()
            pipe = client.pipeline()
            pipe.incr(failed_key)
            pipe.expire(failed_key, LOCKOUT_DURATION_SECONDS)
            res = await pipe.execute()
            new_count = int(res[0])
            logger.info(
                "auth.login_failure_recorded",
                email=clean_email,
                client_ip=client_ip,
                failed_count=new_count,
            )
            return new_count
        except Exception as exc:
            logger.warning("auth.ratelimit_redis_fallback", action="record_failure", error=str(exc))
            self._clean_memory_store()
            bucket = self._memory_store[failed_key]
            if bucket.expires_at <= now:
                bucket.count = 1
                bucket.expires_at = now + LOCKOUT_DURATION_SECONDS
            else:
                bucket.count += 1
                bucket.expires_at = now + LOCKOUT_DURATION_SECONDS
            return bucket.count

    async def clear_login_failures(self, email: str) -> None:
        """Reset consecutive failed login counter upon successful login."""
        clean_email = email.strip().lower()
        failed_key = f"{self._get_key_prefix()}:failed:{clean_email}"
        try:
            client = await redis_manager.get_client()
            await client.delete(failed_key)
        except Exception as exc:
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
                logger.warning("auth.ip_register_throttled", client_ip=client_ip, reg_count=reg_count)
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
        except Exception as exc:
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
