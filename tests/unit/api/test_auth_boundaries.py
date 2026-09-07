"""Unit tests for auth boundaries, readiness, and secret handling (H4)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.dependencies import DEFAULT_USER_ID, get_current_user_id
from app.api.routes.health import check_configuration
from app.domain.errors import AuthenticationError
from app.services.google_auth import GoogleOAuthService
from app.services.spill import LocalFileSpillStore


@pytest.mark.asyncio
async def test_get_current_user_id_blocks_spoofing_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Environment, settings

    # Simulate dev environment with no API key configured
    monkeypatch.setattr(settings.security, "api_key", None)
    monkeypatch.setattr(settings, "environment", Environment.DEVELOPMENT)

    # Without X-User-ID -> resolves to DEFAULT_USER_ID
    uid = await get_current_user_id(x_user_id=None, x_api_key=None)
    assert uid == DEFAULT_USER_ID

    # With X-User-ID == DEFAULT_USER_ID -> succeeds
    uid = await get_current_user_id(x_user_id=DEFAULT_USER_ID, x_api_key=None)
    assert uid == DEFAULT_USER_ID

    # With spoofed X-User-ID -> rejected!
    with pytest.raises(AuthenticationError, match="requires API key authentication"):
        await get_current_user_id(x_user_id="attacker-user", x_api_key=None)


@pytest.mark.asyncio
async def test_get_current_user_id_allows_custom_id_with_valid_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings.security, "api_key", "secret-key-123")

    # With correct API key, custom user id is accepted
    uid = await get_current_user_id(x_user_id="authorized-user", x_api_key="secret-key-123")
    assert uid == "authorized-user"

    # With incorrect API key -> rejected
    with pytest.raises(AuthenticationError, match="A valid API key is required"):
        await get_current_user_id(x_user_id="authorized-user", x_api_key="wrong-key")


def test_check_configuration_validity(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    # Normal settings -> True
    assert check_configuration() is True

    # Missing database URL -> False
    monkeypatch.setattr(settings.database, "url", "")
    assert check_configuration() is False


@pytest.mark.asyncio
async def test_google_auth_disconnect_revokes_both_tokens() -> None:
    session = AsyncMock()
    mock_integration = MagicMock()
    mock_integration.refresh_token_encrypted = "enc-refresh"
    mock_integration.access_token_encrypted = "enc-access"

    service = GoogleOAuthService.__new__(GoogleOAuthService)
    service._get_integration = AsyncMock(return_value=mock_integration)  # type: ignore
    service._decrypt = MagicMock(side_effect=lambda token: f"plain-{token}")  # type: ignore
    service.client = MagicMock()
    service.client.revoke_token = AsyncMock()

    result = await service.disconnect(session, "user-123")
    assert result is True
    assert service.client.revoke_token.await_count == 2
    service.client.revoke_token.assert_any_await("plain-enc-refresh")
    service.client.revoke_token.assert_any_await("plain-enc-access")
    session.delete.assert_awaited_once_with(mock_integration)


@pytest.mark.asyncio
async def test_google_auth_disconnect_fails_open_on_external_revoke_error(caplog) -> None:
    session = AsyncMock()
    mock_integration = MagicMock()
    mock_integration.refresh_token_encrypted = "enc-refresh"
    mock_integration.access_token_encrypted = "enc-access"

    service = GoogleOAuthService.__new__(GoogleOAuthService)
    service._get_integration = AsyncMock(return_value=mock_integration)  # type: ignore
    service._decrypt = MagicMock(side_effect=lambda token: f"plain-{token}")  # type: ignore
    service.client = MagicMock()
    service.client.revoke_token = AsyncMock(side_effect=RuntimeError("Google 503 Service Unavailable"))

    result = await service.disconnect(session, "user-123")
    assert result is True
    # Still deletes local integration so user is not stuck
    session.delete.assert_awaited_once_with(mock_integration)



def test_spill_session_dir_has_64_hex_entropy(tmp_path) -> None:
    store = LocalFileSpillStore(base_dir=tmp_path)
    session_dir = store._session_dir("my-session-id")
    # session-<64-char-hash>
    dir_name = session_dir.name
    assert dir_name.startswith("session-")
    hash_part = dir_name[len("session-") :]
    assert len(hash_part) == 64
