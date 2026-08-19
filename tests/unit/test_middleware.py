"""Unit tests for FastAPI middleware and global error handling."""

import pytest
from httpx import AsyncClient

from app.domain.errors import NotFoundError, PolicyViolationError
from app.main import app


# Add temporary test route to trigger domain error
@app.get("/test-not-found")
async def route_trigger_not_found():
    raise NotFoundError("Test entity not found", details={"id": "abc"})


@app.get("/test-policy-error")
async def route_trigger_policy_error():
    raise PolicyViolationError("Forbidden action requested")


@pytest.mark.asyncio
async def test_request_id_and_timing_headers(async_client: AsyncClient):
    response = await async_client.get("/health", headers={"X-Request-ID": "test-req-12345"})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == "test-req-12345"
    assert "X-Response-Time-Ms" in response.headers


@pytest.mark.asyncio
async def test_automatic_request_id_generation(async_client: AsyncClient):
    response = await async_client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") is not None


@pytest.mark.asyncio
async def test_domain_error_response_format(async_client: AsyncClient):
    response = await async_client.get("/test-not-found")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "NOT_FOUND"
    assert data["error"]["message"] == "Test entity not found"
    assert data["error"]["details"]["id"] == "abc"


@pytest.mark.asyncio
async def test_policy_error_response_format(async_client: AsyncClient):
    response = await async_client.get("/test-policy-error")
    assert response.status_code == 403
    data = response.json()
    assert data["error"]["code"] == "POLICY_VIOLATION"
