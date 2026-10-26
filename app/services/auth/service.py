"""Authentication and session token service with secure password hashing."""

import base64
import functools
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.settings.paths import PROJECT_ROOT
from app.domain.errors import ConfigurationError
from app.infrastructure.db.models import User

logger = structlog.get_logger(__name__)

# PBKDF2 parameters (OWASP recommended floor)
HASH_ALGORITHM = "sha256"
ITERATIONS = 100_000
SALT_BYTES = 16
TOKEN_EXPIRY_SECONDS = 30 * 86400  # 30 days

MIN_SIGNING_KEY_BYTES = 32
_SESSION_KEY_CONTEXT = b"session-token-v1"
_LOCAL_SESSION_SECRET = PROJECT_ROOT / ".secrets" / "session_signing.key"


def _get_signing_key() -> bytes:
    """HMAC key for session tokens, derived from ``SECURITY__APPROVAL_SIGNING_KEY``.

    The configured secret is used under exactly the rule the startup validator enforces
    (at least 32 UTF-8 bytes, taken as given), and a session-specific subkey is derived
    from it so session and approval tokens are never interchangeable. Without such a
    secret, staging/production refuse to sign; development and testing use a random
    local secret, never a value published in source.
    """
    configured = (settings.security.approval_signing_key or "").encode("utf-8")
    if len(configured) >= MIN_SIGNING_KEY_BYTES:
        secret = configured
    elif settings.auth_enforced:
        raise ConfigurationError("SECURITY__APPROVAL_SIGNING_KEY must be at least 32 bytes.")
    else:
        secret = _local_session_secret()
    return hmac.new(secret, _SESSION_KEY_CONTEXT, hashlib.sha256).digest()


@functools.cache
def _local_session_secret() -> bytes:
    """Random secret for local runs without a configured key.

    Kept in the git-ignored ``.secrets/`` directory so sessions survive ``--reload``
    restarts; if that file cannot be written, a per-process secret is used instead.
    """
    try:
        _LOCAL_SESSION_SECRET.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _LOCAL_SESSION_SECRET.open("xb") as handle:
                handle.write(secrets.token_urlsafe(48).encode("ascii"))
            if os.name != "nt":
                _LOCAL_SESSION_SECRET.chmod(0o600)
            logger.warning("session_signing_key_generated", path=str(_LOCAL_SESSION_SECRET))
        except FileExistsError:
            pass
        stored = _LOCAL_SESSION_SECRET.read_bytes().strip()
        if len(stored) >= MIN_SIGNING_KEY_BYTES:
            return stored
    except OSError:
        pass
    logger.warning("session_signing_key_ephemeral", path=str(_LOCAL_SESSION_SECRET))
    return secrets.token_bytes(MIN_SIGNING_KEY_BYTES)


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with a unique random salt."""
    salt = secrets.token_hex(SALT_BYTES)
    key = hashlib.pbkdf2_hmac(
        HASH_ALGORITHM,
        password.encode("utf-8"),
        salt.encode("utf-8"),
        ITERATIONS,
    )
    return f"pbkdf2:{HASH_ALGORITHM}:{ITERATIONS}${salt}${key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored PBKDF2 hash."""
    if not hashed or not password:
        return False
    try:
        scheme, salt_and_hash = hashed.split("$", 1)
        _, algo, iters_str = scheme.split(":")
        salt, expected_hex = salt_and_hash.split("$", 1)
        iterations = int(iters_str)

        computed = hashlib.pbkdf2_hmac(
            algo,
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )
        return secrets.compare_digest(computed.hex(), expected_hex)
    except Exception:  # noqa: BLE001 - fail closed: a malformed hash never verifies
        return False


def create_access_token(user_id: str, email: str, full_name: str | None = None) -> str:
    """Generate a signed HMAC-SHA256 session token."""
    payload = {
        "sub": str(user_id),
        "email": email,
        "name": full_name or "",
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_EXPIRY_SECONDS,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
    signature = hmac.new(
        _get_signing_key(),
        b64_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{b64_payload}.{signature}"


def verify_access_token(token: str) -> dict[str, Any] | None:
    """Validate token signature and expiry; return payload if valid, None otherwise."""
    if not token or "." not in token:
        return None
    try:
        parts = token.strip().split(".")
        if len(parts) != 2:
            return None
        b64_payload, signature = parts
        expected_sig = hmac.new(
            _get_signing_key(),
            b64_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not secrets.compare_digest(expected_sig, signature):
            return None

        # Re-add padding for urlsafe_b64decode
        padded = b64_payload + "=" * (-len(b64_payload) % 4)
        payload_data = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
        if not isinstance(payload_data, dict):
            return None

        if payload_data.get("exp", 0) < time.time():
            return None

        return payload_data
    except Exception:  # noqa: BLE001 - fail closed: a malformed token is no token
        return None


# Dummy hash with 100k iterations used to eliminate side-channel timing attacks
DUMMY_PASSWORD_HASH = (
    "pbkdf2:sha256:100000$0123456789abcdef0123456789abcdef$"
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)


class AuthService:
    """Handles user registration, credential verification, and user resolution."""

    @staticmethod
    async def register(
        session: AsyncSession,
        email: str,
        password: str,
        full_name: str | None = None,
    ) -> User:
        clean_email = email.strip().lower()
        if len(password) < 6:
            raise ValueError("Mật khẩu phải có ít nhất 6 ký tự.")

        stmt = select(User).where(func.lower(User.email) == clean_email)
        res = await session.execute(stmt)
        if res.scalar_one_or_none() is not None:
            raise ValueError("Email này đã được đăng ký tài khoản.")

        user = User(
            id=str(uuid.uuid4()),
            email=clean_email,
            full_name=full_name.strip() if full_name else None,
            hashed_password=hash_password(password),
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def login(
        session: AsyncSession,
        email: str,
        password: str,
    ) -> tuple[User, str]:
        clean_email = email.strip().lower()
        stmt = select(User).where(func.lower(User.email) == clean_email)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        if user is None or not user.hashed_password:
            # Perform dummy verification to consume constant CPU time and prevent timing attacks
            verify_password(password, DUMMY_PASSWORD_HASH)
            raise ValueError("Email hoặc mật khẩu không chính xác.")

        if not verify_password(password, user.hashed_password):
            raise ValueError("Email hoặc mật khẩu không chính xác.")

        if not user.is_active:
            raise ValueError("Email hoặc mật khẩu không chính xác.")

        token = create_access_token(user.id, user.email, user.full_name)
        return user, token

    @staticmethod
    async def get_user_by_id(session: AsyncSession, user_id: str) -> User | None:
        stmt = select(User).where(User.id == user_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
