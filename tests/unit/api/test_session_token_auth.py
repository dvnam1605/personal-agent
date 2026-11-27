"""Session tokens resolve only active accounts, and self-registration is gated by environment."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import dependencies as deps
from app.api.routes.auth import registration_open
from app.core.config import Environment, settings
from app.domain.errors import AuthenticationError
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.services.auth.service import create_access_token


@pytest.fixture
async def factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                User(id="active", email="a@example.com", is_active=True),
                User(id="disabled", email="d@example.com", is_active=False),
            ]
        )
        await session.commit()
    monkeypatch.setattr(deps, "get_session_factory", lambda: session_factory)
    yield session_factory
    await engine.dispose()


async def test_bearer_token_resolves_an_active_account(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    token = create_access_token("active", "a@example.com", None)
    assert await deps.get_current_user_id(authorization=f"Bearer {token}") == "active"


@pytest.mark.parametrize("user_id", ["disabled", "deleted"])
async def test_bearer_token_for_a_disabled_or_missing_account_is_rejected(
    factory: async_sessionmaker[AsyncSession], user_id: str
) -> None:
    token = create_access_token(user_id, f"{user_id}@example.com", None)
    with pytest.raises(AuthenticationError):
        await deps.get_current_user_id(authorization=f"Bearer {token}")


async def test_tampered_bearer_token_never_reaches_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail() -> None:
        raise AssertionError("database must not be queried for an invalid token")

    monkeypatch.setattr(deps, "get_session_factory", _fail)
    with pytest.raises(AuthenticationError):
        await deps.get_current_user_id(authorization="Bearer tampered.token")


@pytest.mark.parametrize(
    ("environment", "configured", "expected"),
    [
        (Environment.DEVELOPMENT, None, True),
        (Environment.TESTING, None, True),
        (Environment.STAGING, None, False),
        (Environment.PRODUCTION, None, False),
        (Environment.PRODUCTION, True, True),
        (Environment.DEVELOPMENT, False, False),
    ],
)
def test_registration_is_closed_outside_development_unless_enabled(
    monkeypatch: pytest.MonkeyPatch,
    environment: Environment,
    configured: bool | None,
    expected: bool,
) -> None:
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings.security, "allow_registration", configured)
    assert registration_open() is expected
