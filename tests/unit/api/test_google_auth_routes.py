"""HTTP-level checks for the Google OAuth routes."""

from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_current_user_id
from app.api.routes.google_auth import get_google_oauth_service
from app.api.routes.google_auth import router as google_auth_router
from app.core.config import GoogleOAuthSettings
from app.services.google.auth import GoogleOAuthService, InMemoryOAuthStateStore


def _google_auth_app() -> FastAPI:
    """App with configured OAuth credentials and no Redis/dotenv dependency."""
    app = FastAPI()
    app.include_router(google_auth_router)
    settings = GoogleOAuthSettings(
        client_id="test-google-client-id.apps.googleusercontent.com",
        client_secret="test-google-client-secret",
        redirect_uri="http://localhost:8000/auth/google/callback",
    )
    service = GoogleOAuthService(settings, state_store=InMemoryOAuthStateStore())
    app.dependency_overrides[get_google_oauth_service] = lambda: service
    app.dependency_overrides[get_current_user_id] = lambda: "default-user"
    return app


@pytest.mark.asyncio
async def test_google_start_uses_configured_credentials_without_leaking_secret() -> None:
    transport = ASGITransport(app=_google_auth_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/google/start")

    assert response.status_code == 307
    location = response.headers["location"]
    assert urlparse(location).hostname == "accounts.google.com"
    assert "client_secret" not in location
    assert "test-google-client-secret" not in location
    assert "code_challenge" in location
    assert "test-google-client-id.apps.googleusercontent.com" in location
