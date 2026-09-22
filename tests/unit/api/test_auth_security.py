"""Unit tests for authentication security hardening: rate limiting, lockout, and timing mitigation."""

from collections.abc import AsyncGenerator
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.infrastructure.db.base import Base
from app.infrastructure.db.session import get_db_session
from app.main import app
from app.services.auth.rate_limiter import MAX_FAILED_LOGIN_ATTEMPTS, auth_rate_limiter
from app.services.auth.service import DUMMY_PASSWORD_HASH, verify_password


@pytest.fixture
async def override_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide isolated in-memory SQLite database session for auth security tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_timing_mitigation_dummy_hash_format() -> None:
    """Verify that DUMMY_PASSWORD_HASH is a valid PBKDF2 hash string and consumes compute time."""
    assert DUMMY_PASSWORD_HASH.startswith("pbkdf2:sha256:100000$")
    is_valid = verify_password("wrong_password_test", DUMMY_PASSWORD_HASH)
    assert is_valid is False


@pytest.mark.asyncio
async def test_rate_limiter_direct_logic() -> None:
    """Verify AuthRateLimiter recording, checking, and clearing failure counters directly."""
    email = "test_logic@example.com"
    ip = "192.168.10.100"

    await auth_rate_limiter.clear_login_failures(email)

    # 1. Allowed initially
    allowed, msg, ttl = await auth_rate_limiter.check_login_allowed(email, ip)
    assert allowed is True
    assert msg is None

    # 2. Record 5 failures
    for i in range(1, MAX_FAILED_LOGIN_ATTEMPTS + 1):
        count = await auth_rate_limiter.record_login_failure(email, ip)
        assert count == i

    # 3. Check now locked
    allowed, msg, ttl = await auth_rate_limiter.check_login_allowed(email, ip)
    assert allowed is False
    assert "tạm khóa" in msg.lower()
    assert ttl > 0

    # 4. Clear failures
    await auth_rate_limiter.clear_login_failures(email)
    allowed, msg, ttl = await auth_rate_limiter.check_login_allowed(email, ip)
    assert allowed is True


@pytest.mark.asyncio
async def test_account_lockout_endpoint(override_db_session: AsyncSession) -> None:
    """Verify that after 5 failed login attempts on /auth/login, account is locked with HTTP 429."""
    async def get_test_session():
        yield override_db_session

    app.dependency_overrides[get_db_session] = get_test_session
    try:
        transport = ASGITransport(app=app)
        headers = {"X-Forwarded-For": "198.51.100.1"}
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
            test_email = "lockout_target@example.com"
            await auth_rate_limiter.clear_login_failures(test_email)

            # 1. First 4 failed attempts should return 401 with remaining count
            for attempt in range(1, MAX_FAILED_LOGIN_ATTEMPTS):
                resp = await client.post(
                    "/auth/login",
                    json={"email": test_email, "password": "wrong_password_123"},
                )
                assert resp.status_code == 401, f"Attempt {attempt} should be 401"
                detail = resp.json().get("detail", "")
                remaining = MAX_FAILED_LOGIN_ATTEMPTS - attempt
                assert str(remaining) in detail

            # 2. 5th failed attempt should trigger lockout (429 Too Many Requests)
            resp_5th = await client.post(
                "/auth/login",
                json={"email": test_email, "password": "wrong_password_123"},
            )
            assert resp_5th.status_code == 429
            assert "Retry-After" in resp_5th.headers
            assert "khóa" in resp_5th.json()["detail"].lower()

            # 3. 6th attempt should also be blocked with 429
            resp_6th = await client.post(
                "/auth/login",
                json={"email": test_email, "password": "even_right_password_is_blocked"},
            )
            assert resp_6th.status_code == 429
            assert "tạm khóa" in resp_6th.json()["detail"].lower()

            await auth_rate_limiter.clear_login_failures(test_email)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_successful_login_clears_failure_counter(override_db_session: AsyncSession) -> None:
    """Verify that a successful login resets any previously accumulated failed attempts."""
    async def get_test_session():
        yield override_db_session

    app.dependency_overrides[get_db_session] = get_test_session
    try:
        transport = ASGITransport(app=app)
        headers = {"X-Forwarded-For": "198.51.100.2"}
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
            test_email = "cleared_user@example.com"
            await auth_rate_limiter.clear_login_failures(test_email)

            # Register user
            reg_resp = await client.post(
                "/auth/register",
                json={"email": test_email, "password": "CorrectPassword123!", "full_name": "Clear User"},
            )
            assert reg_resp.status_code == 201

            # Fail twice
            await client.post("/auth/login", json={"email": test_email, "password": "wrong"})
            await client.post("/auth/login", json={"email": test_email, "password": "wrong"})

            # Successful login
            success_resp = await client.post(
                "/auth/login",
                json={"email": test_email, "password": "CorrectPassword123!"},
            )
            assert success_resp.status_code == 200

            # Next failed attempt should reset and show remaining count as MAX - 1 (e.g. 4)
            fail_again = await client.post("/auth/login", json={"email": test_email, "password": "wrong"})
            assert fail_again.status_code == 401
            assert str(MAX_FAILED_LOGIN_ATTEMPTS - 1) in fail_again.json()["detail"]

            await auth_rate_limiter.clear_login_failures(test_email)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
