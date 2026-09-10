"""Unit tests for audit data redaction and sanitization rules."""

from app.domain.enums import Complexity, Domain, RouteType, RunStatus
from app.domain.models import AssistantState, RouteDecision
from app.services.platform.audit import (
    create_sanitized_state_snapshot,
    mask_email,
    sanitize_payload,
)


def test_mask_email() -> None:
    """Verify email masking preserves first letter and domain."""
    assert mask_email("alice@example.com") == "a***@[REDACTED]"
    assert mask_email("nam.nguyen@company.org") == "n***@[REDACTED]"
    assert mask_email("no email here") == "no email here"


def test_sanitize_payload_secrets_redacted() -> None:
    """Verify secrets and authentication tokens are masked."""
    payload = {
        "user_id": "u1",
        "api_key": "sk-1234567890abcdef",
        "access_token": "bearer xyz987",
        "session_cookie": "sess_abc",
        "password": "supersecretpassword",
        "regular_field": "public_data",
    }
    sanitized = sanitize_payload(payload)

    assert sanitized["user_id"] == "u1"
    assert sanitized["api_key"] == "[REDACTED_SECRET]"
    assert sanitized["access_token"] == "[REDACTED_SECRET]"
    assert sanitized["session_cookie"] == "[REDACTED_SECRET]"
    assert sanitized["password"] == "[REDACTED_SECRET]"
    assert sanitized["regular_field"] == "public_data"


def test_sanitize_payload_chain_of_thought_stripped() -> None:
    """Verify hidden chain-of-thought and internal monologue keys are omitted."""
    payload = {
        "tool_name": "gmail.send",
        "thought": "Thinking about user intent...",
        "reasoning": "Step 1: Check calendar...",
        "hidden_state": {"internal": True},
        "chain_of_thought": "Let's consider all options...",
        "output": "Sent email successfully.",
    }
    sanitized = sanitize_payload(payload)

    assert "thought" not in sanitized
    assert "reasoning" not in sanitized
    assert "hidden_state" not in sanitized
    assert "chain_of_thought" not in sanitized
    assert sanitized["tool_name"] == "gmail.send"
    assert sanitized["output"] == "Sent email successfully."


def test_sanitize_payload_email_and_truncation() -> None:
    """Verify email masking and length truncation in nested payloads."""
    long_text = "A" * 600
    payload = {
        "message": "Contact support at support@google.com for help.",
        "long_field": long_text,
        "nested": {
            "items": [
                {"email": "nam@test.com", "comment": "B" * 550},
            ]
        },
    }
    sanitized = sanitize_payload(payload, max_string_len=100)

    assert "s***@[REDACTED]" in sanitized["message"]
    assert sanitized["long_field"] == ("A" * 100) + "... [TRUNCATED]"
    assert sanitized["nested"]["items"][0]["email"] == "n***@[REDACTED]"
    assert sanitized["nested"]["items"][0]["comment"] == ("B" * 100) + "... [TRUNCATED]"


def test_sanitize_payload_redacts_embedded_credentials_and_drops_unknown_allowlisted_keys() -> None:
    """Verify embedded credentials are masked and callers can enforce a schema allowlist."""
    sanitized = sanitize_payload(
        {
            "status": "running",
            "unknown": "Bearer abc.def.ghi and sk-abcdef123",
        },
        allowed_keys={"status"},
    )

    assert sanitized == {"status": "running"}
    assert sanitize_payload("Bearer abc.def.ghi") == "[REDACTED_SECRET]"


def test_create_sanitized_state_snapshot() -> None:
    """Verify state snapshot sanitization strips secrets and masks emails."""
    route = RouteDecision(
        domains=[Domain.CALENDAR],
        complexity=Complexity.DIRECT,
        route_type=RouteType.DIRECT_SPECIALIST,
        confidence=0.95,
    )
    state = AssistantState(
        run_id="run_snap_1",
        user_id="u_snap",
        request="Please email nam.nguyen@test.com with token sk-9999",
        route_decision=route,
        status=RunStatus.RUNNING,
    )
    snapshot = create_sanitized_state_snapshot(state)

    assert snapshot["run_id"] == "run_snap_1"
    assert "request" not in snapshot
    assert snapshot["status"] == "running"


def test_sanitize_payload_enforces_depth_collection_and_total_size_limits() -> None:
    """Generic metadata cannot grow without bound or recurse indefinitely."""
    payload = {"items": [{"value": "x" * 100} for _ in range(20)]}
    sanitized = sanitize_payload(
        payload,
        max_string_len=100,
        max_items=3,
        max_payload_bytes=200,
    )
    assert len(sanitized["items"]) <= 3
    assert sum(len(str(item)) for item in sanitized["items"]) <= 200

    deeply_nested: dict[str, object] = {"value": "safe"}
    for _ in range(12):
        deeply_nested = {"nested": deeply_nested}
    bounded = sanitize_payload(deeply_nested, max_depth=3)
    assert "[TRUNCATED_DEPTH]" in str(bounded)
