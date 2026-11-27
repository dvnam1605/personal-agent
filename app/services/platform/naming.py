"""Exact, forgiving comparison of names the user types against stored names."""

from __future__ import annotations

from app.core.text import unaccent_vietnamese

_NAME_EDGE = " \t\"'“”‘’.,;:!?"


def normalized_name(value: str) -> str:
    """Fold accents, case, inner spacing and surrounding quotes/punctuation of a name.

    Commands that switch things off (routines, watch rules) match on this value with
    ``==``, never as a substring, so they cannot act on everything that merely contains
    the words the user typed.
    """
    return " ".join(unaccent_vietnamese(value).casefold().split()).strip(_NAME_EDGE)
