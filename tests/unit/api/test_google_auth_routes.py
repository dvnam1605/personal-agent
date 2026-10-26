"""HTTP-level checks for the Google OAuth routes, including browser binding of the flow."""

from collections.abc import AsyncGenerator, Mapping
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user_id
from app.api.routes.google_auth import OAUTH_BINDING_COOKIE, get_google_oauth_service
from app.api.routes.google_auth import router as google_auth_router
from app.core.config import GoogleOAuthSettings
from app.domain.errors import AppError
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import GoogleIntegration
from app.infrastructure.db.session import get_db_session
from app.services.google.auth import GoogleOAuthClient, GoogleOAuthService, InMemoryOAuthStateStore

REDIRECT_URI = "http://localhost:8000/auth/google/callback"
BROWSER_NAVIGATION = {"accept": "text/html", "sec-fetch-dest": "document"}


class FakeGoogleTransport:
    """Token and userinfo endpoints that never leave the process."""

    def __init__(self, scopes: list[str]) -> None:
        self.scopes = scopes

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        raw = kwargs.get("data")
        data: Mapping[str, object] = raw if isinstance(raw, Mapping) else {}
        if data.get("grant_type") == "authorization_code":
            return httpx.Response(
                200,
                json={
                    "access_token": f"access-for-{data.get('code')}",
                    "refresh_token": f"refresh-for-{data.get('code')}",
                    "expires_in": 3600,
                    "scope": " ".join(self.scopes),
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(200)

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(200, json={"sub": "google-subject", "email": "owner@example.com"})


def _service() -> GoogleOAuthService:
    settings = GoogleOAuthSettings(
        client_id="test-google-client-id.apps.googleusercontent.com",
        client_secret="test-google-client-secret",
        redirect_uri=REDIRECT_URI,
        token_encryption_key=Fernet.generate_key().decode("ascii"),
    )
    return GoogleOAuthService(
        settings,
        client=GoogleOAuthClient(settings, transport=FakeGoogleTransport(settings.scopes)),
        state_store=InMemoryOAuthStateStore(),
    )


def _google_auth_app(
    service: GoogleOAuthService,
    *,
    user_id: str | None = "user-a",
    session: AsyncSession | None = None,
) -> FastAPI:
    """App with configured OAuth credentials and no Redis/dotenv dependency.

    ``user_id=None`` keeps the real identity dependency.
    """
    app = FastAPI()

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(google_auth_router)
    app.dependency_overrides[get_google_oauth_service] = lambda: service
    if user_id is not None:
        app.dependency_overrides[get_current_user_id] = lambda: user_id
    if session is not None:

        async def _session() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_db_session] = _session
    return app


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _state(authorization_url: str) -> str:
    return parse_qs(urlparse(authorization_url).query)["state"][0]


@pytest.mark.asyncio
async def test_google_start_uses_configured_credentials_without_leaking_secret() -> None:
    transport = ASGITransport(app=_google_auth_app(_service()))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/auth/google/start")

    assert response.status_code == 200
    location = response.json()["authorization_url"]
    assert urlparse(location).hostname == "accounts.google.com"
    assert "client_secret" not in location
    assert "test-google-client-secret" not in location
    assert "code_challenge" in location
    assert "test-google-client-id.apps.googleusercontent.com" in location


@pytest.mark.asyncio
async def test_google_start_binds_the_state_to_an_httponly_callback_cookie() -> None:
    service = _service()
    transport = ASGITransport(app=_google_auth_app(service))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/auth/google/start")

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{OAUTH_BINDING_COOKIE}=")
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert "Path=/auth/google/callback" in set_cookie
    binding = response.cookies[OAUTH_BINDING_COOKIE]
    record = await service.state_store.consume(
        _state(response.json()["authorization_url"]), binding
    )
    assert record.user_id == "user-a"


@pytest.mark.asyncio
async def test_google_start_no_longer_accepts_a_token_in_the_url() -> None:
    transport = ASGITransport(app=_google_auth_app(_service()))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/google/start", params={"token": "session-token"})

    assert response.status_code == 405


@pytest.mark.asyncio
async def test_google_start_rejects_an_invalid_session_instead_of_falling_back() -> None:
    service = _service()
    transport = ASGITransport(app=_google_auth_app(service, user_id=None))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/google/start", headers={"Authorization": "Bearer tampered.token"}
        )

    assert response.status_code == 401
    assert OAUTH_BINDING_COOKIE not in response.cookies
    assert isinstance(service.state_store, InMemoryOAuthStateStore)
    assert service.state_store._states == {}


@pytest.mark.asyncio
async def test_callback_from_another_browser_cannot_attach_its_google_account(
    async_session: AsyncSession,
) -> None:
    """The attacker starts the flow; the victim's browser (without the cookie) completes it."""
    service = _service()
    attacker_app = _google_auth_app(service, user_id="attacker-1", session=async_session)
    async with AsyncClient(
        transport=ASGITransport(app=attacker_app), base_url="http://test"
    ) as client:
        started = await client.post("/auth/google/start")
    state = _state(started.json()["authorization_url"])

    victim_app = _google_auth_app(service, user_id="victim-1", session=async_session)
    async with AsyncClient(
        transport=ASGITransport(app=victim_app), base_url="http://test"
    ) as victim:
        response = await victim.get(
            "/auth/google/callback",
            params={"code": "victim-code", "state": state},
            headers=BROWSER_NAVIGATION,
        )

    assert response.status_code == 401
    assert (await async_session.execute(select(GoogleIntegration))).scalars().all() == []


@pytest.mark.asyncio
async def test_callback_in_the_starting_browser_stores_the_grant_for_the_starter(
    async_session: AsyncSession,
) -> None:
    service = _service()
    app = _google_auth_app(service, user_id="user-a", session=async_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as browser:
        started = await browser.post("/auth/google/start")
        response = await browser.get(
            "/auth/google/callback",
            params={"code": "user-a-code", "state": _state(started.json()["authorization_url"])},
            headers=BROWSER_NAVIGATION,
        )

    assert response.status_code == 302
    assert f'{OAUTH_BINDING_COOKIE}=""' in response.headers["set-cookie"]
    integrations = (await async_session.execute(select(GoogleIntegration))).scalars().all()
    assert [row.user_id for row in integrations] == ["user-a"]
