"""Unit tests for structured logging and sensitive data censoring."""

from app.core.logging import censor_sensitive_data, get_logger, setup_logging


def test_setup_logging():
    setup_logging()
    logger = get_logger("test.logger")
    assert logger is not None


def test_sensitive_data_censoring():
    event = {
        "event": "user.login",
        "username": "alice",
        "password": "supersecretpassword",
        "api_key": "sk-1234567890",
        "nested": {
            "access_token": "bearer-xyz",
            "safe_field": "public_data",
        },
    }

    censored = censor_sensitive_data(None, "info", event)
    assert censored["password"] == "[REDACTED]"
    assert censored["api_key"] == "[REDACTED]"
    assert censored["username"] == "alice"
    assert censored["nested"]["access_token"] == "[REDACTED]"
    assert censored["nested"]["safe_field"] == "public_data"


def test_embedded_secrets_in_log_values_are_redacted() -> None:
    event = {
        "event": "tool.trace",
        "message": "bearer ya29.a0AfH6SMBsecretvalueXXXX and sk-ant-abc123456789",
    }
    censored = censor_sensitive_data(None, "info", event)
    assert "ya29." not in censored["message"]
    assert "sk-ant-" not in censored["message"]
    assert "[REDACTED_SECRET]" in censored["message"]
