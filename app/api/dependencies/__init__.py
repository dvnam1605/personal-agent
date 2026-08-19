"""Shared FastAPI request dependencies."""

from fastapi import Header

from app.core.config import Environment, settings
from app.domain.errors import AuthenticationError

DEFAULT_USER_ID = "default-user"


async def get_current_user_id(x_user_id: str | None = Header(default=None)) -> str:
    """Resolve the local user identity until the broader user-auth phase is implemented."""
    if x_user_id is None:
        if settings.environment not in (Environment.DEVELOPMENT, Environment.TESTING):
            raise AuthenticationError("A valid user identity is required.")
        user_id = DEFAULT_USER_ID
    else:
        user_id = x_user_id.strip()
    if not user_id or len(user_id) > 128:
        raise AuthenticationError("A valid user identity is required.")
    return user_id


__all__ = ["DEFAULT_USER_ID", "get_current_user_id"]
