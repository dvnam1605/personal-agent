"""Pytest test configuration and shared fixtures."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def async_client():
    """Async test client for FastAPI application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
def _rule_based_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the gateway out of unit tests: multi-domain routing uses the rule pipeline.

    Tests of the agentic path set ``settings.agentic.supervisor_mode`` back to
    ``"agentic"`` and inject a fake graph builder.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings.agentic, "supervisor_mode", "rules")
