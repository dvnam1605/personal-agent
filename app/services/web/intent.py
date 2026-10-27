"""Recognise an explicit request to search the web and extract what to search for."""

from __future__ import annotations

import re

from app.services.skills.matching import unaccent_vietnamese

# Matched on accent-folded text; folding keeps offsets, so the question is cut from the
# original string with its diacritics. Only explicit phrasings count: a question about
# internal documents is never sent to the web provider on the assistant's own initiative.
_PREFIX = re.compile(
    r"^\s*(?:(?:hay |giup (?:toi |minh )?)?(?:tim|tra|tra cuu|search|kiem tra|xem)"
    r"(?: kiem)?(?: thong tin)? (?:tren|qua|bang) (?:web|mang|internet|google)"
    r"|(?:web|google|internet)\s*:"
    r"|search (?:web|google)"
    r")\s*(?:(?:ve|cho)\b)?\s*[:,]?\s*(?P<question>\S.*)$"
)
_NEWS = re.compile(r"^\s*(?P<question>tin tuc\b.*)$")


def parse_web_request(query: str) -> str | None:
    """The question to search for when the user explicitly asks for the web; else ``None``.

    "tìm trên web …", "tra cứu trên mạng về …", "google: …", "tin tức …".
    """
    folded = unaccent_vietnamese(query)
    match = _PREFIX.match(folded) or _NEWS.match(folded)
    if match is None:
        return None
    question = query[match.start("question") :].strip()
    return question or None
