"""Tests for the P5 Google OAuth foundation."""

from collections.abc import AsyncGenerator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import GoogleOAuthSettings
from app.core.security import FernetTokenCipher
from app.domain.errors import AuthenticationError, ValidationError
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import GoogleIntegration
from app.services.google.auth import (
    GoogleOAuthClient,
    GoogleOAuthService,
    GoogleScopeValidator,
    GoogleTokenSet,
    InMemoryOAuthStateStore,
)


class FakeGoogleTransport:
    """Deterministic provider transport that never sends real credentials."""

    def __init__(self, scopes: list[str]) -> None:
        self.scopes = scopes
        self.refresh_calls = 0
        self.revoked_tokens: list[str] = []

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        raw_data = kwargs.get("data")
        data: Mapping[str, object] = raw_data if isinstance(raw_data, Mapping) else {}
        if data.get("grant_type") == "authorization_code":
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token-1",
                    "refresh_token": "refresh-token-1",
                    "expires_in": 3600,
                    "scope": " ".join(self.scopes),
                    "token_type": "Bearer",
                },
            )
        if data.get("grant_type") == "refresh_token":
            self.refresh_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token-2",
                    "expires_in": 3600,
                    "scope": " ".join(self.scopes),
                    "token_type": "Bearer",
                },
            )
        raw_params = kwargs.get("params")
        params: Mapping[str, object] = raw_params if isinstance(raw_params, Mapping) else {}
        token = params.get("token")
        if token:
            self.revoked_tokens.append(str(token))
        return httpx.Response(200)

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(200, json={"sub": "google-subject", "email": "owner@example.com"})


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def make_settings(key: str | None = None) -> GoogleOAuthSettings:
    return GoogleOAuthSettings(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="http://localhost:8000/auth/google/callback",
        scopes=["scope.a", "scope.b"],
        token_encryption_key=key or Fernet.generate_key().decode("ascii"),
    )


@pytest.mark.asyncio
async def test_oauth_state_is_pkce_bound_and_single_use() -> None:
    store = InMemoryOAuthStateStore(ttl_seconds=600)
    settings = make_settings()
    transport = FakeGoogleTransport(settings.scopes)
    service = GoogleOAuthService(
        settings,
        client=GoogleOAuthClient(settings, transport=transport),
        state_store=store,
    )

    url = await service.start("user-1")
    query = parse_qs(urlparse(url).query)
    assert query["state"]
    assert query["code_challenge"]
    assert query["code_challenge_method"] == ["S256"]
    assert "client-secret" not in url

    record = await store.consume(query["state"][0], "user-1")
    assert record.user_id == "user-1"
    with pytest.raises(AuthenticationError):
        await store.consume(query["state"][0], "user-1")


def test_fernet_encryption_does_not_store_plaintext(tmp_path: Path) -> None:
    key_path = tmp_path / "private" / "google.key"
    cipher = FernetTokenCipher.from_key_file(key_path)
    encrypted = cipher.encrypt("refresh-token")

    assert encrypted != "refresh-token"
    assert "refresh-token" not in encrypted
    assert cipher.decrypt(encrypted) == "refresh-token"
    assert key_path.exists()


def test_provider_token_model_excludes_plaintext_from_serialization() -> None:
    token_set = GoogleTokenSet(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=["scope.a"],
    )

    serialized = token_set.model_dump()
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized
    assert "access-token" not in str(serialized)
    assert "refresh-token" not in str(serialized)


@pytest.mark.asyncio
async def test_callback_persists_encrypted_tokens_refreshes_and_disconnects(
    async_session: AsyncSession,
) -> None:
    settings = make_settings()
    transport = FakeGoogleTransport(settings.scopes)
    now = [datetime.now(UTC)]
    service = GoogleOAuthService(
        settings,
        client=GoogleOAuthClient(settings, transport=transport, clock=lambda: now[0]),
        clock=lambda: now[0],
    )

    authorization_url = await service.start("user-1")
    state = parse_qs(urlparse(authorization_url).query)["state"][0]
    connected = await service.callback(async_session, "user-1", "one-time-code", state)
    await async_session.commit()

    assert connected.connected is True
    assert connected.healthy is True
    assert connected.email == "owner@example.com"
    assert set(connected.scopes) == set(settings.scopes)
    assert not hasattr(connected, "access_token")

    integration = (
        await async_session.execute(
            select(GoogleIntegration).where(GoogleIntegration.user_id == "user-1")
        )
    ).scalar_one()
    assert "refresh-token-1" not in integration.refresh_token_encrypted
    assert "access-token-1" not in (integration.access_token_encrypted or "")

    now[0] += timedelta(hours=2)
    access_token = await service.get_access_token(async_session, "user-1")
    assert access_token == "access-token-2"
    assert transport.refresh_calls == 1

    assert await service.disconnect(async_session, "user-1") is True
    await async_session.commit()
    assert set(transport.revoked_tokens) == {"refresh-token-1", "access-token-2"}
    assert (
        await async_session.execute(
            select(GoogleIntegration).where(GoogleIntegration.user_id == "user-1")
        )
    ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_callback_rejects_authorization_response_with_missing_scopes(
    async_session: AsyncSession,
) -> None:
    settings = make_settings()
    transport = FakeGoogleTransport(["scope.a"])
    service = GoogleOAuthService(
        settings,
        client=GoogleOAuthClient(settings, transport=transport),
    )
    authorization_url = await service.start("user-3")
    state = parse_qs(urlparse(authorization_url).query)["state"][0]

    with pytest.raises(ValidationError, match="missing required scopes"):
        await service.callback(async_session, "user-3", "one-time-code", state)


@pytest.mark.asyncio
async def test_missing_scopes_are_reported_and_rejected(async_session: AsyncSession) -> None:
    assert GoogleScopeValidator.missing(["scope.a"], ["scope.a", "scope.b"]) == ["scope.b"]
    with pytest.raises(ValidationError, match="missing required scopes"):
        GoogleScopeValidator.require(["scope.a"], ["scope.a", "scope.b"])


def test_calendar_write_scope_covers_readonly_requirement_but_not_reverse() -> None:
    full_calendar = "https://www.googleapis.com/auth/calendar"
    readonly_calendar = "https://www.googleapis.com/auth/calendar.readonly"

    assert GoogleScopeValidator.missing([full_calendar], [readonly_calendar]) == []
    assert GoogleScopeValidator.missing([readonly_calendar], [full_calendar]) == [full_calendar]


@pytest.mark.asyncio
async def test_status_reports_missing_scopes_without_token_leakage(
    async_session: AsyncSession,
) -> None:
    settings = make_settings()
    cipher = FernetTokenCipher(settings.token_encryption_key or Fernet.generate_key())
    async_session.add(
        GoogleIntegration(
            user_id="user-2",
            refresh_token_encrypted=cipher.encrypt("refresh-token"),
            access_token_encrypted=cipher.encrypt("access-token"),
            scopes=["scope.a"],
        )
    )
    # The status test does not need a User row because it only reads the integration record.
    await async_session.flush()
    service = GoogleOAuthService(settings, cipher=cipher)
    status = await service.status(async_session, "user-2")

    assert status.connected is True
    assert status.healthy is False
    assert status.missing_scopes == ["scope.b"]
    assert "refresh-token" not in status.model_dump_json()
    assert "access-token" not in status.model_dump_json()
