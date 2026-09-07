"""Shared FastAPI request dependencies with fail-closed authentication."""

import secrets

from fastapi import Header

from app.core.config import settings
from app.domain.errors import AuthenticationError

DEFAULT_USER_ID = "default-user"
API_KEY_HEADER = "X-API-Key"
MAX_USER_ID_LENGTH = 128


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
    x_user_id: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> str:
    """Resolve the local user identity only after the request is authorized.

    The ``X-User-ID`` header is never trusted on its own: it is accepted only
    from requests that passed API key authorization. Without an authenticated
    API key, only DEFAULT_USER_ID is permitted to prevent identity spoofing.
    """
    api_key_authenticated = _authorize_request(x_api_key)
    if x_user_id is None:
        user_id = DEFAULT_USER_ID
    else:
        user_id = x_user_id.strip()
        if not api_key_authenticated and user_id != DEFAULT_USER_ID:
            raise AuthenticationError("Custom user identity requires API key authentication.")
    if not user_id or len(user_id) > MAX_USER_ID_LENGTH:
        raise AuthenticationError("A valid user identity is required.")
    return user_id


__all__ = ["API_KEY_HEADER", "DEFAULT_USER_ID", "get_current_user_id"]
