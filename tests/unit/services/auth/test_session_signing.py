"""Session tokens are signed only with a key derived from the operator's secret.

The former code fell back to a literal key in source whenever the stripped key had fewer
than 32 characters, including keys the startup validator accepts (it counts UTF-8 bytes).
"""

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

from app.core.config import Environment, settings
from app.domain.errors import ConfigurationError
from app.services.auth import service

# The literal the old fallback used; anyone with the source could sign with it.
LEGACY_SOURCE_KEY = b"naot-assistant-secure-session-signing-key-32b!"


def _forge(key: bytes, sub: str) -> str:
    now = int(time.time())
    payload = json.dumps(
        {"sub": sub, "email": "x@example.invalid", "name": "", "iat": now, "exp": now + 3600},
        separators=(",", ":"),
    ).encode("utf-8")
    b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{b64}.{hmac.new(key, b64.encode('utf-8'), hashlib.sha256).hexdigest()}"


@pytest.fixture
def local_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    secret_file = tmp_path / "session_signing.key"
    monkeypatch.setattr(service, "_LOCAL_SESSION_SECRET", secret_file)
    service._local_session_secret.cache_clear()
    yield secret_file
    service._local_session_secret.cache_clear()


def test_without_a_key_tokens_use_a_local_secret_not_the_source_literal(
    local_secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.security, "approval_signing_key", None)

    assert service.verify_access_token(_forge(LEGACY_SOURCE_KEY, "victim")) is None
    token = service.create_access_token("user-1", "u@example.invalid")
    assert (service.verify_access_token(token) or {}).get("sub") == "user-1"
    assert local_secret.exists()


def test_a_multibyte_key_the_validator_accepts_is_actually_used(
    local_secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "khoá bí mật của hệ thống nội bộ"
    assert len(key) < 32 <= len(key.encode("utf-8"))
    monkeypatch.setattr(settings.security, "approval_signing_key", key)

    assert service.verify_access_token(_forge(LEGACY_SOURCE_KEY, "victim")) is None
    # Session tokens use a derived subkey, so the raw (approval) key does not sign them either.
    assert service.verify_access_token(_forge(key.encode("utf-8"), "victim")) is None
    token = service.create_access_token("user-1", "u@example.invalid")
    assert (service.verify_access_token(token) or {}).get("sub") == "user-1"
    assert not local_secret.exists()


def test_enforced_environment_with_a_short_key_refuses_to_sign(
    local_secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.security, "approval_signing_key", "  " + "k" * 20)
    monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)

    with pytest.raises(ConfigurationError):
        service.create_access_token("user-1", "u@example.invalid")
    assert service.verify_access_token(_forge(LEGACY_SOURCE_KEY, "victim")) is None
    assert not local_secret.exists()
