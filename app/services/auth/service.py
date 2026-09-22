"""Authentication and session token service with secure password hashing."""

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.db.models import User

# PBKDF2 parameters (OWASP recommended floor)
HASH_ALGORITHM = "sha256"
ITERATIONS = 100_000
SALT_BYTES = 16
TOKEN_EXPIRY_SECONDS = 30 * 86400  # 30 days


def _get_signing_key() -> bytes:
    """Retrieve secret key for HMAC token signing."""
    key = settings.security.approval_signing_key or ""
    if len(key.strip()) >= 32:
        return key.strip().encode("utf-8")
    # Fallback to deterministic key in local dev
    return b"naot-assistant-secure-session-signing-key-32b!"


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
    except Exception:
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
    except Exception:
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
