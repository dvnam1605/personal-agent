"""Text helpers for notification bodies built from untrusted provider data."""

from __future__ import annotations

import re

_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()<>#+!|~>])")
_WHITESPACE = re.compile(r"\s+")


def plain(value: object, max_len: int = 200) -> str:
    """Render mail subjects, sender names and event titles as inert one-line text.

    These strings come from other people (anyone can send a mail or an invite), and the
    notification body is shown as Markdown, so links, images and formatting characters
    are escaped and line breaks collapsed rather than rendered.
    """
    text = _WHITESPACE.sub(" ", str(value or "")).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return _MARKDOWN_SPECIALS.sub(r"\\\1", text)
