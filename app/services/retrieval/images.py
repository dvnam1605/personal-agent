"""Put the images of cited evidence back into a finished answer.

The model reads an image only as its description, so it never writes image
references itself. For each cited evidence ``[n]``, its images are aligned,
in source order, to the answer lines citing ``[n]``: an image goes under the
line sharing the most distinctive words with its description and with the
source sentence before it (words rare among that evidence's images, so
"Dism++" or "cửa sổ" decide nothing). A step-by-step answer then shows each
screenshot under its step; an image matching no line stays next to its
neighbours, or follows the last line citing its evidence. Every image is
shown once; images of uncited evidence are not shown.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from app.core.images import IMAGE_LINE, IMAGE_REF, image_ref
from app.core.text import unaccent_vietnamese

MAX_IMAGES = 12
CONTEXT_CHARS = 200
ALT_WEIGHT = 3
# A match needs a description word, or two words of the source sentence.
MIN_SCORE = 2
# Word overlap with its old line for an image group to go back under a line.
RESTORE_MIN_SIMILARITY = 0.3
_BADGE = re.compile(r"\[(\d+)\]")
_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = frozenset(
    "cac cua cho voi trong tren duoi nay day duoc nguoi dung hinh chup mo ta tu dong "
    "dang la va de khi thi neu tiep tuc chon nhan bam".split()
)

# (alt, key, source text just before the image) in source order.
ImageRefs = list[tuple[str, str, str]]


def refs_in(text: str) -> ImageRefs:
    refs: ImageRefs = []
    last = 0
    for m in IMAGE_REF.finditer(text):
        refs.append((m.group("alt"), m.group("key"), text[last : m.start()][-CONTEXT_CHARS:]))
        last = m.end()
    return refs


def _words(text: str) -> set[str]:
    words = _WORD.findall(unaccent_vietnamese(IMAGE_REF.sub("", text)).lower())
    return {w for w in words if len(w) > 2 and not w.isdigit()} - _STOP


def _align(scores: list[list[int]]) -> list[int | None]:
    """Line index for each image, non-decreasing in image order, maximising the shared words.

    ``scores[i][j]`` is image ``i`` against candidate line ``j``; an image
    scoring below ``MIN_SCORE`` everywhere on its path is left unassigned (None).
    """
    k, m = len(scores), len(scores[0]) if scores else 0
    best = [[0] * (m + 1) for _ in range(k + 1)]
    for i in range(1, k + 1):
        for j in range(1, m + 1):
            score = scores[i - 1][j - 1]
            take = best[i - 1][j] + score if score >= MIN_SCORE else -1
            best[i][j] = max(best[i][j - 1], best[i - 1][j], take)
    assigned: list[int | None] = [None] * k
    i, j = k, m
    while i > 0 and j > 0:
        if best[i][j] == best[i][j - 1]:
            j -= 1
        elif best[i][j] == best[i - 1][j]:
            i -= 1
        else:
            assigned[i - 1] = j - 1
            i -= 1
    return assigned


def interleave(text: str, images_for: Callable[[int], ImageRefs]) -> str:
    """The answer with each cited evidence's images under the lines they belong to."""
    lines = text.split("\n")
    cited = list(dict.fromkeys(int(n) for line in lines for n in _BADGE.findall(line)))
    after: dict[int, list[str]] = {}  # line index → image references
    shown: set[str] = set()
    for number in cited:
        images = [ref for ref in images_for(number) if ref[1] not in shown]
        candidates = [j for j, line in enumerate(lines) if f"[{number}]" in line]
        if not images or not candidates:
            continue
        described = [(_words(alt), _words(context)) for alt, _, context in images]
        counts: dict[str, int] = {}
        for alt_words, context_words in described:
            for word in alt_words | context_words:
                counts[word] = counts.get(word, 0) + 1
        limit = max(1, len(images) // 4)
        distinctive = {w for w, n in counts.items() if n <= limit}
        line_words = [_words(lines[j]) & distinctive for j in candidates]
        # What the image shows outweighs the text laid out next to it.
        assigned = _align(
            [
                [ALT_WEIGHT * len(lw & alt_words) + len(lw & context_words) for lw in line_words]
                for alt_words, context_words in described
            ]
        )
        # No line is about any of these images (a refusal citing the guide, an
        # aside): show none. Otherwise unmatched images stay with the next
        # matched one, else the previous one.
        if all(slot is None for slot in assigned):
            continue
        fallback = max(slot for slot in assigned if slot is not None)
        for i in range(len(assigned) - 1, -1, -1):
            if assigned[i] is None:
                assigned[i] = fallback
            else:
                fallback = assigned[i]
        for (alt, key, _), slot in zip(images, assigned, strict=True):
            if key in shown or len(shown) >= MAX_IMAGES or slot is None:
                continue
            shown.add(key)
            after.setdefault(candidates[slot], []).append(image_ref(alt, key))
    if not after:
        return text
    out: list[str] = []
    for j, line in enumerate(lines):
        out.append(line)
        out.extend(after.get(j, []))
    return "\n".join(out)


def split_images(text: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """Text without its image lines, and each group of image lines with the line it followed.

    Lets a caller rewrite or filter the prose (quality gates, review) and put
    the images back afterwards with :func:`restore_images`.
    """
    groups: list[tuple[str, list[str]]] = []
    kept: list[str] = []
    for line in text.split("\n"):
        if IMAGE_LINE.match(line):
            host = kept[-1] if kept else ""
            if groups and groups[-1][0] == host:
                groups[-1][1].append(line.strip())
            else:
                groups.append((host, [line.strip()]))
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip(), groups


def restore_images(text: str, groups: list[tuple[str, list[str]]]) -> str:
    """Put image groups back under the lines most like the ones they followed.

    Searches forward only, so the images keep their order; a group whose line
    is gone (removed by a gate or rewritten past recognition) goes under the
    last placed group, or at the end.
    """
    if not groups:
        return text
    lines = text.split("\n")
    words = [_words(line) for line in lines]
    after: dict[int, list[str]] = {}
    start = 0
    for host, refs in groups:
        host_words = _words(host)
        best, best_score = None, 0.0
        for j in range(start, len(lines)):
            if host_words and words[j]:
                score = len(host_words & words[j]) / len(host_words | words[j])
                if score > best_score:
                    best, best_score = j, score
        slot = best if best is not None and best_score >= RESTORE_MIN_SIMILARITY else None
        if slot is None:
            slot = max(after) if after else len(lines) - 1
        after.setdefault(slot, []).extend(refs)
        start = slot
    out: list[str] = []
    for j, line in enumerate(lines):
        out.append(line)
        out.extend(after.get(j, []))
    return "\n".join(out)
