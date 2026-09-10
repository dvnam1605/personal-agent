"""Unit tests for typed application configuration."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Environment, GoogleOAuthSettings, SecuritySettings, Settings


def test_default_settings_instantiation():
    settings = Settings()
    assert settings.app_name == "Personal AI Assistant"
    assert settings.app_version == "0.1.0"
    assert settings.environment in [Environment.DEVELOPMENT, Environment.TESTING]
    assert settings.api_prefix == "/api/v1"


def test_database_settings():
    settings = Settings()
    assert "postgresql" in settings.database.url
    assert settings.database.pool_size == 10
    assert settings.database.max_overflow == 20


def test_budget_settings():
    settings = Settings()
    assert settings.react_budget.max_steps == 6
    assert settings.react_budget.max_tool_calls == 8
    assert settings.supervisor_budget.max_replans == 2
    assert settings.supervisor_budget.timeout_seconds == 30.0
    assert settings.supervisor_budget.cost_per_task_usd == 0.02
    assert settings.llm_budget.direct_specialist.target_llm_calls == 1
    assert settings.llm_budget.supervisor_dag.max_tokens == 16000


def test_timeouts_settings():
    settings = Settings()
    assert settings.timeouts.google_api_seconds == 10.0
    assert settings.timeouts.llm_request_seconds == 30.0


def test_parse_debug_various_inputs():
    """Verify robust parse_debug parsing of boolean and string inputs."""
    assert Settings.parse_debug(True) is True
    assert Settings.parse_debug(False) is False
    assert Settings.parse_debug("true") is True
    assert Settings.parse_debug("TRUE") is True
    assert Settings.parse_debug("1") is True
    assert Settings.parse_debug("debug") is True
    assert Settings.parse_debug("release") is False
    assert Settings.parse_debug("0") is False
    assert Settings.parse_debug("false") is False
    assert Settings.parse_debug("anything_else") is False


def test_google_client_secrets_file_loads_credentials_without_serializing_secret(
    tmp_path: Path,
) -> None:
    """The downloaded Google JSON supplies credentials while config dumps stay redacted."""
    client_file = tmp_path / "client_secret.json"
    client_file.write_text(
        json.dumps(
            {
                "web": {
                    "client_id": "test-client-id",
                    "client_secret": "test-client-secret",
                }
            }
        ),
        encoding="utf-8",
    )

    google = GoogleOAuthSettings(client_secrets_file=str(client_file))

    assert google.client_id == "test-client-id"
    assert google.client_secret == "test-client-secret"
    assert "client_secret" not in google.model_dump()


def test_cors_allowlist_rejects_wildcard() -> None:
    with pytest.raises(ValidationError, match="must not include"):
        SecuritySettings(cors_allowed_origins=["*"])


def test_docs_and_redoc_disabled_in_production(monkeypatch) -> None:
    from app.core.config import Environment, settings
    from app.main import create_app

    monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)
    monkeypatch.setattr(settings, "debug", False)
    application = create_app()
    assert application.docs_url is None
    assert application.redoc_url is None
    assert application.openapi_url is None


def test_compose_requires_database_and_redis_passwords() -> None:
    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD" in text
    assert "REDIS_PASSWORD:?Set REDIS_PASSWORD" in text
    assert ":-postgres" not in text
    assert "--requirepass" in text


def test_development_rejects_remote_database_url() -> None:
    with pytest.raises(ValidationError, match="not local"):
        Settings(
            environment=Environment.DEVELOPMENT,
            database={"url": "postgresql+asyncpg://u:p@db.prod.internal:5432/assistant"},
            _env_file=None,  # type: ignore[call-arg]
        )


def test_deploy_target_production_rejects_development_environment() -> None:
    with pytest.raises(ValidationError, match="DEPLOY_TARGET"):
        Settings(
            environment=Environment.DEVELOPMENT,
            deploy_target="production",
            _env_file=None,  # type: ignore[call-arg]
        )


def test_production_requires_api_key_user_binding() -> None:
    with pytest.raises(ValidationError, match="API_KEY_USER_ID"):
        Settings(
            environment=Environment.PRODUCTION,
            database={
                "url": "postgresql+asyncpg://app:StrongSecret123!@db.internal:5432/assistant"
            },
            redis={"url": "redis://:redis-secret@redis.internal:6379/0"},
            security=SecuritySettings(api_key="shared-key", approval_signing_key="a" * 32),
            _env_file=None,  # type: ignore[call-arg]
        )


def test_production_signing_key_never_falls_back_to_test_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings as app_settings
    from app.services.approvals.tokens import _TEST_SIGNING_KEY, _get_signing_key

    monkeypatch.setattr(app_settings, "environment", Environment.PRODUCTION)
    monkeypatch.setattr(app_settings.security, "approval_signing_key", None)
    with pytest.raises(ValueError, match="APPROVAL_SIGNING_KEY"):
        _get_signing_key()
    monkeypatch.setattr(app_settings.security, "approval_signing_key", "p" * 32)
    assert _get_signing_key() != _TEST_SIGNING_KEY
