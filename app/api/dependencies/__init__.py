"""Shared FastAPI request dependencies with fail-closed authentication."""

import logging
import secrets

from fastapi import Header

from app.core.config import settings
from app.domain.errors import AuthenticationError
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_session_factory
from app.services.auth.service import verify_access_token

DEFAULT_USER_ID = "default-user"
API_KEY_HEADER = "X-API-Key"
MAX_USER_ID_LENGTH = 128

logger = logging.getLogger(__name__)


def _authorize_request(x_api_key: str | None) -> bool:
    """Enforce the shared API key boundary for requests without a session token.

    - When a key is configured, it is required on every such request.
    - Returns True if authenticated via a configured API key.
    - Without a configured key, only development/testing may proceed; staging
      and production fail closed instead of trusting spoofable headers.
    """
    expected_key = settings.security.api_key
    if expected_key:
        provided = (x_api_key or "").strip()
        if not provided or not secrets.compare_digest(provided, expected_key):
            raise AuthenticationError("Cần API key hợp lệ.")
        return True
    if settings.auth_enforced:
        raise AuthenticationError(
            "Máy chủ chưa cấu hình xác thực; từ chối truy cập không xác thực."
        )
    return False


async def _active_account_id(user_id: str) -> str | None:
    """Return the id only if the account still exists and is active.

    A session token is a signed claim made at login; looking the account up on every
    request is what lets deactivating or deleting a user cut off its live tokens.
    """
    async with get_session_factory()() as session:
        user = await session.get(User, user_id)
    return user.id if user is not None and user.is_active else None


async def get_current_user_id(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> str:
    """Resolve the local user identity only after the request is authorized.

    If an Authorization: Bearer <token> header is present, it takes precedence: the
    token must verify and name an account that is still active. Otherwise, falls back
    to API Key / X-User-ID.
    """
    if isinstance(authorization, str) and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        payload = verify_access_token(token)
        account_id = (
            await _active_account_id(str(payload["sub"])) if payload and "sub" in payload else None
        )
        if account_id is None:
            raise AuthenticationError("Phiên đăng nhập đã hết hạn hoặc không hợp lệ.")
        return account_id

    api_key_authenticated = _authorize_request(x_api_key)
    bound = (settings.security.api_key_user_id or "").strip() or None
    if x_user_id is not None and not x_user_id.strip():
        raise AuthenticationError("Cần danh tính người dùng hợp lệ.")
    header_id = x_user_id.strip() if x_user_id else None

    if api_key_authenticated and bound:
        if header_id and header_id != bound:
            logger.error(
                "api_key_user_id_mismatch",
                extra={"bound_user_id": bound, "requested_user_id": header_id},
            )
            raise AuthenticationError("X-User-ID không khớp với người dùng gắn với API key.")
        user_id = bound
    elif header_id is None:
        user_id = DEFAULT_USER_ID
    else:
        user_id = header_id
        if not api_key_authenticated and user_id != DEFAULT_USER_ID:
            raise AuthenticationError("Chỉ định người dùng riêng cần xác thực bằng API key.")
        if api_key_authenticated and settings.auth_enforced:
            logger.error(
                "api_key_missing_user_binding",
                extra={"requested_user_id": user_id},
            )
            raise AuthenticationError("API key chưa được gắn với người dùng nào.")

    if not user_id or len(user_id) > MAX_USER_ID_LENGTH:
        raise AuthenticationError("Cần danh tính người dùng hợp lệ.")
    return user_id


__all__ = ["API_KEY_HEADER", "DEFAULT_USER_ID", "get_current_user_id"]
