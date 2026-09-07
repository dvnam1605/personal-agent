"""Bounded privacy sanitization for persisted and externally supplied JSON data."""

import logging
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

SECRET_KEY_PATTERN = re.compile(
    r"(?i)(token|secret|password|api[_-]?key|authorization|cookie|session|bearer|private[_-]?key)"
)
EMAIL_PATTERN = re.compile(r"\b([a-zA-Z0-9_.+-])[a-zA-Z0-9_.+-]*@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b")
CHAIN_OF_THOUGHT_KEYS = {
    "thought",
    "reasoning",
    "hidden_state",
    "chain_of_thought",
    "internal_monologue",
    "raw_thinking",
}
SAFE_NON_SECRET_KEYS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "tool_call_count",
    "llm_call_count",
    "retry_count",
    "state_version",
}
EMBEDDED_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+"),
    re.compile(r"\b(?:sk|rk)-[a-zA-Z0-9_-]+\b"),
    re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[a-zA-Z0-9_]{8,}\b"),
    re.compile(r"\bxox[baprs]-[a-zA-Z0-9-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bya29\.[0-9A-Za-z_-]{20,}\b"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.IGNORECASE | re.DOTALL,
    ),
)

DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_ITEMS = 100
DEFAULT_MAX_PAYLOAD_BYTES = 32_768


def mask_email(value: str) -> str:
    """Mask email addresses while retaining the first character and domain."""
    return EMAIL_PATTERN.sub(r"\1***@\2", value)


def sanitize_string(value: str, max_string_len: int = 500) -> str:
    """Redact embedded credentials, mask email addresses, and cap string length."""
    sanitized = value
    for pattern in EMBEDDED_SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
    sanitized = mask_email(sanitized)
    if len(sanitized) > max_string_len:
        return sanitized[:max_string_len] + "... [TRUNCATED]"
    return sanitized


class _SanitizationBudget:
    """Track aggregate output size while recursively sanitizing one payload."""

    def __init__(self, max_payload_bytes: int) -> None:
        if max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be positive")
        self.remaining = max_payload_bytes

    def consume(self, value: Any) -> bool:
        size = len(str(value).encode("utf-8"))
        if size > self.remaining:
            self.remaining = 0
            return False
        self.remaining -= size
        return True


def sanitize_payload(
    data: Any,
    max_string_len: int = 500,
    allowed_keys: set[str] | None = None,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> Any:
    """Recursively sanitize JSON-like data with depth, collection, and size limits.

    ``allowed_keys`` applies to the outermost mapping. Callers that need a narrower
    nested schema should sanitize that nested mapping independently.
    """
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    if max_items <= 0:
        raise ValueError("max_items must be positive")
    budget = _SanitizationBudget(max_payload_bytes)
    return _sanitize_value(
        data,
        max_string_len=max_string_len,
        allowed_keys=allowed_keys,
        depth=0,
        max_depth=max_depth,
        max_items=max_items,
        budget=budget,
    )


def _sanitize_value(
    data: Any,
    *,
    max_string_len: int,
    allowed_keys: set[str] | None,
    depth: int,
    max_depth: int,
    max_items: int,
    budget: _SanitizationBudget,
) -> Any:
    if depth > max_depth:
        return "[TRUNCATED_DEPTH]"

    if isinstance(data, Mapping):
        sanitized: dict[str, Any] = {}
        for index, (raw_key, value) in enumerate(data.items()):
            if index >= max_items:
                logger.warning(
                    "sanitization_dict_items_truncated",
                    extra={"max_items": max_items, "total_items": len(data)},
                )
                break
            key = str(raw_key)
            if depth == 0 and allowed_keys is not None and key not in allowed_keys:
                continue
            if key.lower() in CHAIN_OF_THOUGHT_KEYS:
                continue
            if not budget.consume(key):
                logger.warning("sanitization_budget_exhausted")
                break
            if SECRET_KEY_PATTERN.search(key) and key.lower() not in SAFE_NON_SECRET_KEYS:
                sanitized[key] = "[REDACTED_SECRET]"
                budget.consume(sanitized[key])
                continue
            value_result = _sanitize_value(
                value,
                max_string_len=max_string_len,
                allowed_keys=None,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                budget=budget,
            )
            sanitized[key] = value_result
            if budget.remaining <= 0:
                logger.warning("sanitization_budget_exhausted")
                break
        return sanitized

    if isinstance(data, (list, tuple, set, frozenset)):
        raw_list = list(data)
        if len(raw_list) > max_items:
            logger.warning(
                "sanitization_list_items_truncated",
                extra={"max_items": max_items, "total_items": len(raw_list)},
            )
        sanitized_list: list[Any] = []
        for item in raw_list[:max_items]:
            if budget.remaining <= 0:
                logger.warning("sanitization_budget_exhausted")
                break
            sanitized_list.append(
                _sanitize_value(
                    item,
                    max_string_len=max_string_len,
                    allowed_keys=None,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    budget=budget,
                )
            )
        return sanitized_list

    if isinstance(data, str):
        value = sanitize_string(data, max_string_len)
        if budget.consume(value):
            return value
        return "[TRUNCATED_PAYLOAD]"

    if isinstance(data, (datetime, date)):
        value = data.isoformat()
        return value if budget.consume(value) else "[TRUNCATED_PAYLOAD]"

    if isinstance(data, Decimal):
        value = str(data)
        return value if budget.consume(value) else "[TRUNCATED_PAYLOAD]"

    if isinstance(data, (int, float, bool)) or data is None:
        if budget.consume(data):
            return data
        return "[TRUNCATED_PAYLOAD]"

    value = sanitize_string(str(data), max_string_len)
    return value if budget.consume(value) else "[TRUNCATED_PAYLOAD]"
