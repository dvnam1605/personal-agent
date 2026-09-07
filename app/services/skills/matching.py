"""Capability and trigger matching helpers for dynamic skills."""

from __future__ import annotations

import re
import unicodedata

from app.tools.registry import capability_matches

# ``đ``/``Đ`` are atomic letters (not a combining stroke), so NFKD leaves them.
_VIETNAMESE_STROKES = str.maketrans({"đ": "d", "Đ": "D"})
_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)


def capability_is_satisfied(required: str, available: set[str]) -> bool:
    """Return True when any available pattern covers the required capability."""
    needed = required.strip()
    if not needed:
        return False
    return any(capability_matches(owned, needed) for owned in available if owned.strip())


def capabilities_are_satisfied(required: list[str], available: set[str]) -> bool:
    """Return True when every required capability is covered by ``available``."""
    return all(capability_is_satisfied(item, available) for item in required)


def unaccent_vietnamese(text: str) -> str:
    """Fold case, strip Vietnamese diacritics, and collapse whitespace."""
    folded = text.translate(_VIETNAMESE_STROKES)
    decomposed = unicodedata.normalize("NFKD", folded)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def trigger_matches(trigger: str, query: str) -> bool:
    """Match a trigger phrase, ``/regex/`` literal, or unaccented substring.

    Plain-phrase matching folds Vietnamese diacritics (P10 FTS posture) so
    ``chuan bi hop`` still hits ``chuẩn bị họp``. When the folded phrase is
    not a contiguous substring, every trigger token of length ≥ 2 must appear
    in the query (word-order / filler variants such as ``chuan bi cuoc hop``).
    """
    needle = trigger.strip()
    haystack = query.strip()
    if not needle or not haystack:
        return False
    if len(needle) >= 2 and needle.startswith("/") and needle.endswith("/"):
        try:
            return re.search(needle[1:-1], haystack, flags=re.IGNORECASE) is not None
        except re.error:
            return False
    needle_n = unaccent_vietnamese(needle)
    haystack_n = unaccent_vietnamese(haystack)
    if not needle_n or not haystack_n:
        return False
    if needle_n in haystack_n:
        return True
    needle_tokens = {token for token in _TOKEN_RE.findall(needle_n) if len(token) >= 2}
    if not needle_tokens:
        return False
    haystack_tokens = set(_TOKEN_RE.findall(haystack_n))
    return needle_tokens <= haystack_tokens
