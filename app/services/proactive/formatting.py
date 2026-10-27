"""Text helpers for notification bodies built from untrusted provider data."""

from __future__ import annotations

import re

from app.core.text import inert_markdown

_WHITESPACE = re.compile(r"\s+")
_ADDRESS = re.compile(r"\s*<[^<>@\s]+@[^<>\s]+>\s*$")


def plain(value: object, max_len: int = 200) -> str:
    """Render mail subjects, sender names and event titles as inert one-line text.

    These strings come from other people (anyone can send a mail or an invite) and the
    notification body is shown as Markdown, so line breaks are collapsed and link and
    formula syntax is broken up; the text itself stays readable, without escapes.
    """
    text = _WHITESPACE.sub(" ", str(value or "")).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return inert_markdown(text)


def sender_name(value: object) -> str:
    """ "Anh Nam <nam@x.vn>" → "Anh Nam"; a bare address stays as it is."""
    text = str(value or "").strip()
    name = _ADDRESS.sub("", text).strip().strip('"')
    return plain(name or text, 120)
