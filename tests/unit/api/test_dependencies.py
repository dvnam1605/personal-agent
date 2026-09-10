"""Tests for the fail-closed API key dependency protecting every route."""

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

import app.api.dependencies as deps
from app.core.config import Environment, SecuritySettings, Settings
from app.domain.errors import AppError


def _app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.get("/whoami")
    async def whoami(user_id: str = Depends(deps.get_current_user_id)) -> dict[str, str]:
        return {"user_id": user_id}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


_TEST_APPROVAL_KEY = "unit-test-approval-signing-key-32b"


def _settings(
    env: Environment, api_key: str | None, *, api_key_user_id: str | None = None
) -> Settings:
    extra = {}
    if env in (Environment.PRODUCTION, Environment.STAGING):
        extra["database"] = {
            "url": "postgresql+asyncpg://secure_app_user:StrongSecret123!@db-prod.internal:5432/assistant"
        }
        extra["redis"] = {"url": "redis://:redis-secret@redis.internal:6379/0"}
        if api_key and api_key_user_id is None:
            api_key_user_id = "bound-user"
    return Settings(
        environment=env,
        security=SecuritySettings(
            api_key=api_key,
            api_key_user_id=api_key_user_id,
            approval_signing_key=_TEST_APPROVAL_KEY,
        ),
        _env_file=None,  # type: ignore[call-arg]
        **extra,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("header_value", [None, "", "wrong-key"])
async def test_missing_or_wrong_api_key_is_rejected_when_configured(header_value):
    original = deps.settings
    deps.settings = _settings(Environment.DEVELOPMENT, "secret-key")
    try:
        headers = {"X-API-Key": header_value} if header_value else {}
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/whoami", headers=headers)
        assert response.status_code == 401
    finally:
        deps.settings = original


@pytest.mark.asyncio
async def test_valid_api_key_binds_configured_user_identity():
    original = deps.settings
    deps.settings = _settings(Environment.PRODUCTION, "secret-key", api_key_user_id="alice")
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            ok = await client.get("/whoami", headers={"X-API-Key": "secret-key"})
            match = await client.get(
                "/whoami", headers={"X-API-Key": "secret-key", "X-User-ID": "alice"}
            )
            mismatch = await client.get(
                "/whoami", headers={"X-API-Key": "secret-key", "X-User-ID": "victim"}
            )
        assert ok.status_code == 200 and ok.json()["user_id"] == "alice"
        assert match.status_code == 200 and match.json()["user_id"] == "alice"
        assert mismatch.status_code == 401
    finally:
        deps.settings = original


@pytest.mark.asyncio
@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PRODUCTION])
async def test_unconfigured_server_fails_closed_outside_local_envs(env):
    original = deps.settings
    deps.settings = _settings(env, None)
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            spoofed = await client.get("/whoami", headers={"X-User-ID": "attacker"})
            default = await client.get("/whoami")
        assert spoofed.status_code == 401
        assert default.status_code == 401
    finally:
        deps.settings = original


@pytest.mark.asyncio
async def test_development_without_key_keeps_default_user():
    original = deps.settings
    deps.settings = _settings(Environment.DEVELOPMENT, None)
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            default = await client.get("/whoami")
            explicit = await client.get("/whoami", headers={"X-User-ID": "bob "})
        assert default.status_code == 200
        assert default.json()["user_id"] == deps.DEFAULT_USER_ID
        # Without an authenticated API key, unverified custom identity spoofing is rejected
        assert explicit.status_code == 401
    finally:
        deps.settings = original


def test_user_id_length_bound_enforced(monkeypatch):
    monkeypatch.setattr(deps, "settings", _settings(Environment.DEVELOPMENT, "test-key"))

    async def call_with(user_id: str, api_key: str | None = "test-key") -> Exception | None:
        try:
            await deps.get_current_user_id(x_user_id=user_id, x_api_key=api_key)
        except Exception as exc:  # noqa: BLE001 - test probe
            return exc
        return None

    long_id = "x" * (deps.MAX_USER_ID_LENGTH + 1)
    assert asyncio_run(call_with(long_id)) is not None
    assert asyncio_run(call_with("")) is not None
    assert asyncio_run(call_with("ok-user")) is None
    assert asyncio_run(call_with("ok-user", api_key=None)) is not None


def asyncio_run(coro):  # noqa: ANN001
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)
