"""Google OAuth authorization and connection-management endpoints."""

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.core.config import Environment, settings
from app.domain.errors import ValidationError as DomainValidationError
from app.infrastructure.db.session import get_db_session
from app.infrastructure.redis.client import redis_manager
from app.services.auth.service import verify_access_token
from app.services.google.auth import (
    GoogleIntegrationStatus,
    GoogleOAuthService,
    InMemoryOAuthStateStore,
    OAuthStateStore,
    RedisOAuthStateStore,
)

router = APIRouter(prefix="/auth/google", tags=["Google Authentication"])


def build_default_google_oauth_service() -> GoogleOAuthService:
    """Select the OAuth state backend matching the deployment environment.

    TESTING keeps the process-local store. Development, staging, and production
    share single-use state through Redis so multi-worker deploys cannot drop
    the authorization-code callback (L2).
    """
    state_store: OAuthStateStore
    if settings.environment is Environment.TESTING:
        state_store = InMemoryOAuthStateStore(ttl_seconds=settings.google.oauth_state_ttl_seconds)
    else:
        state_store = RedisOAuthStateStore(
            redis_manager.get_client,
            ttl_seconds=settings.google.oauth_state_ttl_seconds,
        )
    return GoogleOAuthService(settings.google, state_store=state_store)


google_oauth_service = build_default_google_oauth_service()


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
    token: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    service: GoogleOAuthService = Depends(get_google_oauth_service),  # noqa: B008
) -> RedirectResponse:
    """Start the Google authorization-code flow."""
    resolved_user_id = user_id
    if token:
        payload = verify_access_token(token)
        if payload and "sub" in payload:
            resolved_user_id = str(payload["sub"])
    authorization_url = await service.start(resolved_user_id)
    return RedirectResponse(url=authorization_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/callback", response_model=GoogleCallbackResponse)
async def google_auth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    service: GoogleOAuthService = Depends(get_google_oauth_service),  # noqa: B008
) -> Response | GoogleIntegrationStatus:
    """Complete the OAuth flow and persist encrypted Google tokens."""
    if not state:
        raise DomainValidationError("Google authorization callback did not include state.")
    integration_status = await service.callback(session, user_id, code, state, error)

    # If invoked by a browser navigating directly to the callback, redirect back to the web application
    accept = request.headers.get("accept", "")
    sec_fetch_dest = request.headers.get("sec-fetch-dest", "")
    if "text/html" in accept or sec_fetch_dest == "document":
        redirect_target = "http://localhost:5173/"
        if request.url.port == 5173:
            redirect_target = "/"
        return RedirectResponse(url=redirect_target, status_code=status.HTTP_302_FOUND)

    return integration_status


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


__all__ = ["build_default_google_oauth_service", "get_google_oauth_service", "router"]
