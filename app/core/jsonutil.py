"""Canonical JSON helpers backed by orjson (L1)."""

from __future__ import annotations

import orjson

_SORT = orjson.OPT_SORT_KEYS


def dumps_canonical(value: object) -> bytes:
    """Serialize *value* to sorted-key JSON bytes for hashing and HMAC payloads."""
    return orjson.dumps(value, option=_SORT)


def dumps_utf8(value: object) -> str:
    """Serialize *value* to a UTF-8 JSON string (Redis / HTTP bodies)."""
    return dumps_canonical(value).decode("utf-8")


def loads(data: bytes | str) -> object:
    """Parse JSON bytes or text."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return orjson.loads(data)
