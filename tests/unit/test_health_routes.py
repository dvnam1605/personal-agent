"""Readiness endpoint behavior with healthy and degraded dependencies."""

import pytest
from httpx import AsyncClient

import app.api.routes.health as health_module


@pytest.mark.asyncio
async def test_health_check_endpoint(async_client: AsyncClient):
    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "environment" in data


@pytest.mark.asyncio
async def test_api_prefix_health_endpoint(async_client: AsyncClient):
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_readiness_ready_when_dependencies_healthy(async_client: AsyncClient, monkeypatch):
    async def ok_database() -> bool:
        return True

    async def ok_redis() -> bool:
        return True

    monkeypatch.setattr(health_module, "check_database", ok_database)
    monkeypatch.setattr(health_module, "check_redis", ok_redis)

    response = await async_client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["database"]["status"] == "ok"
    assert data["checks"]["redis"]["status"] == "ok"
    assert data["checks"]["configuration"]["status"] == "ok"


@pytest.mark.asyncio
async def test_readiness_returns_503_when_dependency_degraded(
    async_client: AsyncClient, monkeypatch
):
    async def broken_database() -> bool:
        return False

    async def ok_redis() -> bool:
        return True

    monkeypatch.setattr(health_module, "check_database", broken_database)
    monkeypatch.setattr(health_module, "check_redis", ok_redis)

    response = await async_client.get("/ready")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["checks"]["database"]["status"] == "error"


@pytest.mark.asyncio
async def test_real_dependency_probes_return_bool_without_raising():
    """Live probes must degrade to a boolean verdict instead of raising."""
    db_ok = await health_module.check_database()
    redis_ok = await health_module.check_redis()
    assert isinstance(db_ok, bool)
    assert isinstance(redis_ok, bool)
