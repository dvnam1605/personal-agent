"""Capability and trigger matching helpers for dynamic skills and workflows (M3 / M9)."""

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


def match_trigger(
    trigger: str,
    query: str,
    *,
    unaccented_query: str | None = None,
    allow_token_subset: bool = False,
) -> bool:
    """Unified trigger matching across skills and static workflows (M3 / N1).

    Supports:
    1. Literal ``/regex/`` syntax with case-insensitivity.
    2. Contiguous phrase matching with word boundaries on unaccented Vietnamese.
    3. Optional token-subset matching when ``allow_token_subset=True``.
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
    haystack_n = unaccented_query if unaccented_query is not None else unaccent_vietnamese(haystack)
    if not needle_n or not haystack_n:
        return False

    # Exact or contiguous word-boundary match
    if needle_n == haystack_n:
        return True
    pattern = r"(?:\A|\s)" + re.escape(needle_n) + r"(?:\Z|\s|[.,!?;:])"
    if re.search(pattern, haystack_n) is not None:
        return True

    if allow_token_subset:
        needle_tokens = {token for token in _TOKEN_RE.findall(needle_n) if len(token) >= 2}
        if not needle_tokens:
            return False
        haystack_tokens = set(_TOKEN_RE.findall(haystack_n))
        return needle_tokens <= haystack_tokens

    return False


def trigger_matches(
    trigger: str,
    query: str,
    *,
    unaccented_query: str | None = None,
    allow_token_subset: bool = True,
) -> bool:
    """Skill trigger matching delegate (defaults to allow_token_subset=True)."""
    return match_trigger(
        trigger,
        query,
        unaccented_query=unaccented_query,
        allow_token_subset=allow_token_subset,
    )


def match_workflow_trigger(trigger: str, query: str) -> bool:
    """Strict workflow trigger matching delegate (strictly contiguous, allow_token_subset=False)."""
    return match_trigger(trigger, query, allow_token_subset=False)
