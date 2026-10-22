"""Universal Vietnamese orthographic and phonetic normalization.

Provides dynamic, dictionary-free generation of Vietnamese spelling variants
(such as y/i vowel interchangeability and tone placement conventions)
as well as standard Unicode NFC normalization.
"""

from __future__ import annotations

import re
import unicodedata
from itertools import product

# Map of tone-marked Y to corresponding tone-marked I
_Y_TO_I_CHARS: dict[str, str] = {
    "y": "i",
    "ỳ": "ì",
    "ý": "í",
    "ỷ": "ỉ",
    "ỹ": "ĩ",
    "ỵ": "ị",
    "Y": "I",
    "Ỳ": "Ì",
    "Ý": "Í",
    "Ỷ": "Ỉ",
    "Ỹ": "Ĩ",
    "Ỵ": "Ị",
}

_I_TO_Y_CHARS: dict[str, str] = {v: k for k, v in _Y_TO_I_CHARS.items()}

# Consonants that allow y/i interchangeability in standard Vietnamese
# (e.g., sỹ/sĩ, kỹ/kĩ, lý/lí, mỹ/mĩ, tỷ/tỉ, quý/quí, thúy/thuý, kỳ/kì, hy/hi)
_INTERCHANGEABLE_ONSET_REGEX = re.compile(
    r"\b(s|k|l|m|t|v|h|b|d|đ|r|qu|th|ch|nh|ph|tr|kh)([yỳýỷỹỵiìíỉĩị])\b",
    re.IGNORECASE,
)

# Tone mark placement variations for diphthongs (new vs old orthography):
# e.g., hòa <-> hoà, thủy <-> thuỷ, khỏe <-> khoẻ, toàn <-> toan...
_TONE_PLACEMENT_PAIRS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bhòa\b", re.IGNORECASE), "hoà"),
    (re.compile(r"\bhoà\b", re.IGNORECASE), "hòa"),
    (re.compile(r"\bhóa\b", re.IGNORECASE), "hoá"),
    (re.compile(r"\bhoá\b", re.IGNORECASE), "hóa"),
    (re.compile(r"\bhỏa\b", re.IGNORECASE), "hoả"),
    (re.compile(r"\bhoả\b", re.IGNORECASE), "hỏa"),
    (re.compile(r"\bhọa\b", re.IGNORECASE), "hoạ"),
    (re.compile(r"\bhoạ\b", re.IGNORECASE), "họa"),
    (re.compile(r"\bthúy\b", re.IGNORECASE), "thuý"),
    (re.compile(r"\bthuý\b", re.IGNORECASE), "thúy"),
    (re.compile(r"\bthủy\b", re.IGNORECASE), "thuỷ"),
    (re.compile(r"\bthuỷ\b", re.IGNORECASE), "thủy"),
    (re.compile(r"\bthụy\b", re.IGNORECASE), "thuỵ"),
    (re.compile(r"\bthuỵ\b", re.IGNORECASE), "thụy"),
    (re.compile(r"\bkhỏe\b", re.IGNORECASE), "khoẻ"),
    (re.compile(r"\bkhoẻ\b", re.IGNORECASE), "khỏe"),
]


def normalize_unicode(text: str) -> str:
    """Normalize text to standard Unicode NFC form."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", text.strip())


def generate_single_word_variants(word: str) -> list[str]:
    """Generate orthographic variants for a single Vietnamese word (e.g. 'sỹ' -> ['sỹ', 'sĩ'])."""
    norm = normalize_unicode(word)
    variants = [norm]

    # 1. Check y <-> i interchangeability after consonants
    m = _INTERCHANGEABLE_ONSET_REGEX.match(norm)
    if m:
        onset, vowel = m.groups()
        if vowel in _Y_TO_I_CHARS:
            alt_vowel = _Y_TO_I_CHARS[vowel]
            variants.append(f"{onset}{alt_vowel}")
        elif vowel in _I_TO_Y_CHARS:
            alt_vowel = _I_TO_Y_CHARS[vowel]
            variants.append(f"{onset}{alt_vowel}")

    return list(dict.fromkeys(variants))


def generate_orthographic_variants(phrase: str, max_variants: int = 6) -> list[str]:
    """Dynamically generate all orthographic spelling variants for a phrase or name.

    Works without hardcoding: given 'Đỗ Tiến Sỹ', generates ['đỗ tiến sỹ', 'đỗ tiến sĩ'].
    Given 'Nguyễn Kỹ Thuật', generates ['nguyễn kỹ thuật', 'nguyễn kĩ thuật'].

    Args:
        phrase: The input phrase, name, or search query.
        max_variants: Maximum number of combinations returned to prevent explosion.

    Returns:
        list[str]: Deduplicated lowercase variants starting with the original form.
    """
    cleaned = normalize_unicode(phrase).lower()
    if not cleaned:
        return []

    words = cleaned.split()
    word_variant_lists: list[list[str]] = []

    for w in words:
        v = generate_single_word_variants(w)
        word_variant_lists.append(v)

    # Compute cartesian product of word variants
    combinations = product(*word_variant_lists)
    results: list[str] = []
    seen: set[str] = set()

    for combo in combinations:
        variant = " ".join(combo)
        if variant not in seen:
            seen.add(variant)
            results.append(variant)
            if len(results) >= max_variants:
                break

    # Add tone-placement variants if applicable
    tone_extras: list[str] = []
    for var in list(results):
        for pat, replacement in _TONE_PLACEMENT_PAIRS:
            if pat.search(var):
                alt = pat.sub(replacement, var)
                if alt not in seen and len(results) + len(tone_extras) < max_variants:
                    seen.add(alt)
                    tone_extras.append(alt)

    results.extend(tone_extras)
    return results
