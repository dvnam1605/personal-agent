"""HTTP-level checks for the Google OAuth routes."""

from urllib.parse import urlparse

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_google_start_uses_configured_credentials_without_leaking_secret(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/auth/google/start")

    assert response.status_code == 307
    location = response.headers["location"]
    assert urlparse(location).hostname == "accounts.google.com"
    assert "client_secret" not in location
    assert "code_challenge" in location
