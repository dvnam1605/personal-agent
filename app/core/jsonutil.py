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


def extract_json_object(raw: str) -> dict:
    """Parse the first JSON object in an LLM reply (code fences / prose tolerated)."""
    import json

    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("LLM reply contains no JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM reply is not a JSON object")
    return data
