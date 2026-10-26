"""Regression tests for login throttling: atomic lockout and trusted client-address derivation."""

import asyncio
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.api.routes.auth import get_client_ip
from app.core.config import settings
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import get_db_session
from app.main import app
from app.services.auth import rate_limiter
from app.services.auth.rate_limiter import MAX_FAILED_LOGIN_ATTEMPTS, auth_rate_limiter
from app.services.auth.service import AuthService


@pytest.fixture(autouse=True)
def memory_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the limiter's in-memory path so a developer's live Redis cannot skew counts."""

    async def no_redis() -> None:
        raise ConnectionError("redis disabled for this test")

    monkeypatch.setattr(rate_limiter.redis_manager, "get_client", no_redis)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def _request(peer: str, forwarded_for: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
    return Request({"type": "http", "headers": headers, "client": (peer, 40000)})


@pytest.mark.asyncio
async def test_concurrent_attempts_cannot_exceed_the_lockout_limit() -> None:
    """A burst of simultaneous attempts on one account admits at most the lockout limit."""
    email = "burst@example.com"
    await auth_rate_limiter.clear_login_failures(email)

    results = await asyncio.gather(
        *(auth_rate_limiter.check_login_allowed(email, f"203.0.113.{i}") for i in range(12))
    )

    assert sum(1 for allowed, _, _ in results if allowed) == MAX_FAILED_LOGIN_ATTEMPTS
    await auth_rate_limiter.clear_login_failures(email)


@pytest.mark.asyncio
async def test_concurrent_logins_evaluate_at_most_the_lockout_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twelve parallel wrong passwords for one account verify at most five of them."""
    await AuthService.register(
        session=db_session, email="target@example.com", password="right-password-1"
    )
    await db_session.commit()
    evaluated = 0
    original_login = AuthService.login

    async def counting_login(**kwargs: object):
        nonlocal evaluated
        evaluated += 1
        return await original_login(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(AuthService, "login", staticmethod(counting_login))

    async def session_override() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = session_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/auth/login",
                        json={"email": "target@example.com", "password": f"guess-{i}"},
                    )
                    for i in range(12)
                )
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        await auth_rate_limiter.clear_login_failures("target@example.com")

    assert evaluated <= MAX_FAILED_LOGIN_ATTEMPTS
    assert all(r.status_code in (401, 429) for r in responses)


def test_forwarded_for_from_an_untrusted_peer_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.security, "trusted_proxies", [])

    assert get_client_ip(_request("198.51.100.7", "203.0.113.99")) == "198.51.100.7"


def test_trusted_proxy_yields_the_rightmost_untrusted_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client cannot pick the throttle key by prepending its own X-Forwarded-For entries."""
    monkeypatch.setattr(settings.security, "trusted_proxies", ["10.249.20.10/32"])

    spoofed_then_real = _request("10.249.20.10", "1.2.3.4, 198.51.100.7")
    assert get_client_ip(spoofed_then_real) == "198.51.100.7"
    assert get_client_ip(_request("10.249.20.10")) == "10.249.20.10"


@pytest.mark.asyncio
async def test_rotating_forwarded_for_does_not_bypass_the_register_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.security, "trusted_proxies", [])

    async def session_override() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = session_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            statuses = [
                (
                    await client.post(
                        "/api/v1/auth/register",
                        json={"email": f"user{i}@example.com", "password": "dummy-pass-1"},
                        headers={"X-Forwarded-For": f"198.51.100.{i}"},
                    )
                ).status_code
                for i in range(12)
            ]
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert statuses == [201] * 10 + [429] * 2
