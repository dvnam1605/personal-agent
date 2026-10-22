"""Column types and sanitizers shared by the ORM models."""

from typing import Any

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

from app.core.sanitization import sanitize_payload, sanitize_string

# JSONB on PostgreSQL (indexable, migration 0011), plain JSON elsewhere (SQLite tests).
_JSONB = JSON().with_variant(JSONB(), "postgresql")


def _sanitize_approval_arguments(value: Any) -> dict[str, Any]:
    """Bound approval JSON while keeping recipient emails executable."""
    sanitized = sanitize_payload(
        value if isinstance(value, dict) else {},
        max_string_len=1000,
        max_depth=8,
        max_items=100,
        max_payload_bytes=32_768,
        mask_emails=False,
    )
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_json_mapping(value: Any) -> dict[str, Any]:
    """Keep JSON metadata bounded even when models are created outside a service."""
    sanitized = sanitize_payload(
        value if isinstance(value, dict) else {},
        max_string_len=1000,
        max_depth=8,
        max_items=100,
        max_payload_bytes=32_768,
    )
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_document_metadata(value: Any) -> dict[str, Any]:
    """Keep document/chunk metadata bounded without truncating administrative structures."""
    sanitized = sanitize_payload(
        value if isinstance(value, dict) else {},
        max_string_len=8192,
        max_depth=10,
        max_items=150,
        max_payload_bytes=32_768,
    )
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_bounded_text(value: str | None, max_len: int) -> str | None:
    """Sanitize a bounded SQL string without exceeding its column width."""
    return None if value is None else sanitize_string(value, max_len)[:max_len]
