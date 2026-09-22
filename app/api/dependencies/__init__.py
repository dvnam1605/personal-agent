"""Shared FastAPI request dependencies with fail-closed authentication."""

import logging
import secrets

from fastapi import Header

from app.core.config import settings
from app.domain.errors import AuthenticationError
from app.services.auth.service import verify_access_token

DEFAULT_USER_ID = "default-user"
API_KEY_HEADER = "X-API-Key"
MAX_USER_ID_LENGTH = 128

logger = logging.getLogger(__name__)


def _authorize_request(x_api_key: str | None) -> bool:
    """Enforce the shared API key boundary before any identity is resolved.

    - A configured key is required on every request in every environment.
    - Returns True if authenticated via a configured API key.
    - Without a configured key, only development/testing may proceed; staging
      and production fail closed instead of trusting spoofable headers.
    """
    expected_key = settings.security.api_key
    if expected_key:
        provided = (x_api_key or "").strip()
        if not provided or not secrets.compare_digest(provided, expected_key):
            raise AuthenticationError("A valid API key is required.")
        return True
    if settings.auth_enforced:
        raise AuthenticationError(
            "Server authentication is not configured; refusing unauthenticated access."
        )
    return False


async def get_current_user_id(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> str:
    """Resolve the local user identity only after the request is authorized.

    If an Authorization: Bearer <token> header is present, it takes precedence
    and resolves the authenticated account. Otherwise, falls back to API Key / X-User-ID.
    """
    if isinstance(authorization, str) and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        payload = verify_access_token(token)
        if payload and "sub" in payload:
            return str(payload["sub"])
        raise AuthenticationError("Phiên đăng nhập đã hết hạn hoặc không hợp lệ.")

    api_key_authenticated = _authorize_request(x_api_key)
    bound = (settings.security.api_key_user_id or "").strip() or None
    if x_user_id is not None and not x_user_id.strip():
        raise AuthenticationError("A valid user identity is required.")
    header_id = x_user_id.strip() if x_user_id else None

    if api_key_authenticated and bound:
        if header_id and header_id != bound:
            logger.error(
                "api_key_user_id_mismatch",
                extra={"bound_user_id": bound, "requested_user_id": header_id},
            )
            raise AuthenticationError("X-User-ID does not match the API key binding.")
        user_id = bound
    elif header_id is None:
        user_id = DEFAULT_USER_ID
    else:
        user_id = header_id
        if not api_key_authenticated and user_id != DEFAULT_USER_ID:
            raise AuthenticationError("Custom user identity requires API key authentication.")
        if api_key_authenticated and settings.auth_enforced:
            logger.error(
                "api_key_missing_user_binding",
                extra={"requested_user_id": user_id},
            )
            raise AuthenticationError("API key is not bound to a user identity.")

    if not user_id or len(user_id) > MAX_USER_ID_LENGTH:
        raise AuthenticationError("A valid user identity is required.")
    return user_id


__all__ = ["API_KEY_HEADER", "DEFAULT_USER_ID", "get_current_user_id"]
