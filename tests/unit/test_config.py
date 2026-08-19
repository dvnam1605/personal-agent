"""Unit tests for typed application configuration."""

import json
from pathlib import Path

from app.core.config import Environment, GoogleOAuthSettings, Settings


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
