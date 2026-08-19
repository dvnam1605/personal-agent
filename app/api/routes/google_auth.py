"""Google OAuth authorization and connection-management endpoints."""

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.core.config import settings
from app.domain.errors import ValidationError as DomainValidationError
from app.infrastructure.db.session import get_db_session
from app.services.google_auth import GoogleIntegrationStatus, GoogleOAuthService

router = APIRouter(prefix="/auth/google", tags=["Google Authentication"])
google_oauth_service = GoogleOAuthService(settings.google)


def get_google_oauth_service() -> GoogleOAuthService:
    """Dependency seam for tests and future application wiring."""
    return google_oauth_service


class GoogleCallbackResponse(BaseModel):
    """Safe callback response without OAuth token material."""

    model_config = ConfigDict(extra="forbid")

    connected: bool
    healthy: bool
    email: str | None = None
    scopes: list[str]
    missing_scopes: list[str]


class GoogleDisconnectResponse(BaseModel):
    """Safe disconnect result."""

    disconnected: bool


@router.get("/start", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
async def start_google_auth(
    user_id: str = Depends(get_current_user_id),
    service: GoogleOAuthService = Depends(get_google_oauth_service),  # noqa: B008
) -> RedirectResponse:
    """Start the Google authorization-code flow."""
    authorization_url = await service.start(user_id)
    return RedirectResponse(url=authorization_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/callback", response_model=GoogleCallbackResponse)
async def google_auth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    service: GoogleOAuthService = Depends(get_google_oauth_service),  # noqa: B008
) -> GoogleIntegrationStatus:
    """Complete the OAuth flow and persist encrypted Google tokens."""
    if not state:
        raise DomainValidationError("Google authorization callback did not include state.")
    return await service.callback(session, user_id, code, state, error)


@router.get("/status", response_model=GoogleIntegrationStatus)
async def google_auth_status(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    service: GoogleOAuthService = Depends(get_google_oauth_service),  # noqa: B008
) -> GoogleIntegrationStatus:
    """Return local Google connection health and scope status."""
    return await service.status(session, user_id)


@router.delete("/disconnect", response_model=GoogleDisconnectResponse)
async def disconnect_google(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    service: GoogleOAuthService = Depends(get_google_oauth_service),  # noqa: B008
) -> GoogleDisconnectResponse:
    """Revoke Google access and remove the local encrypted connection."""
    return GoogleDisconnectResponse(disconnected=await service.disconnect(session, user_id))


__all__ = ["get_google_oauth_service", "router"]
