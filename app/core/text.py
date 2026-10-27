"""Dependency-free text helpers shared across layers (ingestion, retrieval, routing).

Kept as a leaf module so low-level code (profiling, SQL compilers) can fold
Vietnamese text without importing the tools/skills packages, which pull in
the retrieval pipeline and would create import cycles.
"""

from __future__ import annotations

import re
import unicodedata

# ``đ``/``Đ`` are atomic letters (not a combining stroke), so NFKD leaves them.
_VIETNAMESE_STROKES = str.maketrans({"đ": "d", "Đ": "D"})


def unaccent_vietnamese(text: str) -> str:
    """Fold case, strip Vietnamese diacritics, and collapse whitespace."""
    folded = text.translate(_VIETNAMESE_STROKES)
    decomposed = unicodedata.normalize("NFKD", folded)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


# The web app's Markdown renderer has no backslash escapes, so untrusted text is made
# inert by breaking markup up instead: "](" cannot form a link or image, "$" cannot open
# a formula.
_LINK_JOIN = re.compile(r"\]\s*\(")


def inert_markdown(text: str) -> str:
    """Untrusted text that renders as itself in the web app's Markdown, no escapes shown."""
    return _LINK_JOIN.sub("] (", text).replace("$", "＄")
