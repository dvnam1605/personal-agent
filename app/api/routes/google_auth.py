"""Google OAuth authorization and connection-management endpoints.

The flow is started by an authenticated POST from the web app, never by a navigation
carrying a credential in the URL. Starting sets an HttpOnly binding cookie whose hash
is stored with the single-use state; Google's redirect to the callback carries no
application credentials, so the callback trusts only that state and cookie pair.
"""

import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.core.config import Environment, settings
from app.domain.errors import ValidationError as DomainValidationError
from app.infrastructure.db.session import get_db_session
from app.infrastructure.redis.client import redis_manager
from app.services.google.auth import (
    GoogleIntegrationStatus,
    GoogleOAuthService,
    InMemoryOAuthStateStore,
    OAuthStateStore,
    RedisOAuthStateStore,
)

router = APIRouter(prefix="/auth/google", tags=["Google Authentication"])

OAUTH_BINDING_COOKIE = "google_oauth_binding"


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


class GoogleStartResponse(BaseModel):
    """Provider authorization URL the web app navigates to after starting the flow."""

    model_config = ConfigDict(extra="forbid")

    authorization_url: str


def _binding_cookie_path(service: GoogleOAuthService) -> str:
    """Scope the binding cookie to the configured callback path."""
    return urlparse(service.settings.redirect_uri).path or "/"


@router.post("/start", response_model=GoogleStartResponse)
async def start_google_auth(
    response: Response,
    user_id: str = Depends(get_current_user_id),
    service: GoogleOAuthService = Depends(get_google_oauth_service),  # noqa: B008
) -> GoogleStartResponse:
    """Start the authorization-code flow for the caller and bind it to this browser.

    The owner is the identity resolved from the request credentials; an invalid or
    expired session token is rejected by the dependency instead of falling back to
    another identity.
    """
    browser_binding = secrets.token_urlsafe(32)
    authorization_url = await service.start(user_id, browser_binding)
    response.set_cookie(
        OAUTH_BINDING_COOKIE,
        browser_binding,
        max_age=service.settings.oauth_state_ttl_seconds,
        path=_binding_cookie_path(service),
        secure=urlparse(service.settings.redirect_uri).scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return GoogleStartResponse(authorization_url=authorization_url)


@router.get("/callback", response_model=GoogleCallbackResponse)
async def google_auth_callback(
    request: Request,
    response: Response,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    service: GoogleOAuthService = Depends(get_google_oauth_service),  # noqa: B008
) -> Response | GoogleIntegrationStatus:
    """Complete the OAuth flow for the browser that started it and persist encrypted tokens."""
    if not state:
        raise DomainValidationError("Google authorization callback did not include state.")
    integration_status = await service.callback(
        session, code, state, request.cookies.get(OAUTH_BINDING_COOKIE), error
    )
    cookie_path = _binding_cookie_path(service)

    # If invoked by a browser navigating directly to the callback, redirect back to the web application
    accept = request.headers.get("accept", "")
    sec_fetch_dest = request.headers.get("sec-fetch-dest", "")
    if "text/html" in accept or sec_fetch_dest == "document":
        redirect_target = "http://localhost:5173/"
        if request.url.port == 5173:
            redirect_target = "/"
        redirect = RedirectResponse(url=redirect_target, status_code=status.HTTP_302_FOUND)
        redirect.delete_cookie(OAUTH_BINDING_COOKIE, path=cookie_path)
        return redirect

    response.delete_cookie(OAUTH_BINDING_COOKIE, path=cookie_path)
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


__all__ = [
    "OAUTH_BINDING_COOKIE",
    "build_default_google_oauth_service",
    "get_google_oauth_service",
    "router",
]
