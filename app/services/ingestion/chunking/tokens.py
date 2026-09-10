"""Deterministic token estimation, normalization and sentence utilities.

Pure functions only (spec P9C objective). The estimator is character-based,
zero-dependency, and calibrated against the real XLM-RoBERTa tokenizer shipped
with ``AITeamVN/Vietnamese_Embedding`` (benchmark 2026-08-24, recorded in the
P09C review pack): plain Vietnamese prose lands at ~0.95-1.00x of chars/4,
English prose ~1.08x, and table/markdown structure up to ~1.75x because pipe
and heading glyphs fragment into many word-pieces. The estimator therefore
prices structural glyphs separately so construction budgets stay conservative
exactly where real token counts explode.

Benchmark status: re-run against the production embedding tokenizer
2026-08-24 (P09C review M1) — the coefficients above ARE the calibrated
result. Re-benchmark only if the embedding snapshot is replaced, and pair
that with a chunker version bump so affected documents reindex.
"""

from __future__ import annotations

import math
import re
import unicodedata

_CHARS_PER_TOKEN = 4
_PROSE_SAFETY = 1.08
_PIPE_UNIT_WEIGHT = 2
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+")
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_PIPES_RE = re.compile(r"\|")
_GLYPHS_RE = re.compile(r"[`>\-*#]")


class TextMetrics:
    """Cheap structural counts enabling O(1) join-size estimation (L6)."""

    __slots__ = ("chars", "pipes", "glyphs")

    def __init__(self, chars: int, pipes: int, glyphs: int) -> None:
        self.chars = chars
        self.pipes = pipes
        self.glyphs = glyphs


_EMPTY_METRICS = TextMetrics(0, 0, 0)


def text_metrics(text: str) -> TextMetrics:
    return TextMetrics(len(text), len(_PIPES_RE.findall(text)), len(_GLYPHS_RE.findall(text)))


def estimate_from_metrics(metrics: TextMetrics, *, joiners: int = 0) -> int:
    """Token estimate for a text (or '\\n\\n'-join of texts with ``joiners`` gaps).

    Each joiner contributes two characters and no structure.
    """
    chars = metrics.chars + 2 * joiners
    prose_chars = max(chars - metrics.pipes - metrics.glyphs, 0)
    approx = (prose_chars / _CHARS_PER_TOKEN) * _PROSE_SAFETY + (
        metrics.pipes * _PIPE_UNIT_WEIGHT + metrics.glyphs
    )
    return max(1, math.ceil(approx))


def estimate_tokens(text: str) -> int:
    """Conservative approximate token count for mixed prose/markdown text."""
    if not text:
        return 0
    return estimate_from_metrics(text_metrics(text))


def sum_metrics(metrics: list[TextMetrics]) -> TextMetrics:
    return TextMetrics(
        sum(m.chars for m in metrics),
        sum(m.pipes for m in metrics),
        sum(m.glyphs for m in metrics),
    )


def normalize_text(text: str) -> str:
    """Allowed cleanup only: whitespace normalization + parser-artifact removal.

    Forbidden operations (flatten-to-one-string, heading removal, metadata
    drops, generative rewriting) are structurally impossible here.
    """
    cleaned = _ZERO_WIDTH.sub("", text)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def split_sentences(text: str) -> list[str]:
    """Split on terminal punctuation / newlines; keeps every character."""
    pieces = [piece.strip() for piece in _SENTENCE_BOUNDARY.split(text) if piece.strip()]
    if len(pieces) <= 1:
        pieces = [segment.strip() for segment in text.split("\n") if segment.strip()]
    return [unicodedata.normalize("NFC", piece) for piece in pieces]


def truncate_at_token_boundary(text: str, max_tokens: int) -> tuple[str, int]:
    """Last-resort hard cut near ``max_tokens``.

    Returns ``(prefix, consumed_chars)`` where ``consumed_chars`` covers the
    prefix BEFORE trailing-whitespace stripping so callers can slice the
    remainder losslessly (review L3).
    """
    if max_tokens < 1:
        raise ValueError("max_tokens must be >= 1")
    limit = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= limit:
        return text, len(text)
    raw_cut = text[:limit]
    return raw_cut.rstrip(), limit
