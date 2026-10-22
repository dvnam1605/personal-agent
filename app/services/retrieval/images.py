"""Put the images of the evidence into an answer.

The model sees each image as its label and description, "(Hình 4 — mô tả tự
động: …)", and writes ``[Hình 4]`` under the step the image illustrates —
it knows which step that is and when an answer needs no picture at all.
:class:`ImageMarkers` turns each marker into the image, only for images of
the evidence given (cited evidence first), each once; invented labels vanish.

:func:`interleave` is the fallback for a step-by-step answer the model left
without markers: images are aligned, in source order, to the lines sharing
the most distinctive features with their description.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from app.core.images import IMAGE_LINE, IMAGE_REF, image_ref
from app.core.text import unaccent_vietnamese

MAX_IMAGES = 12
CONTEXT_CHARS = 200
ALT_WEIGHT = 3
# The sentence laid out before an image may describe the next step: it counts at most
# as much as one description feature.
CONTEXT_CAP = 3
# A match needs a description feature, or three of the source sentence.
MIN_SCORE = 3
# Word overlap with its old line for an image group to go back under a line.
RESTORE_MIN_SIMILARITY = 0.3
_BADGE = re.compile(r"\[(\d+)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"\w+", re.UNICODE)
# Vietnamese syllables say little alone ("ung", "hop", "theo"), so features are
# words written without diacritics (Toolkit, Accept, x86, ActiveBackup) and
# syllable pairs ("hop thoai", "google drive"), minus the vocabulary every
# screenshot description shares.
_STOP = frozenset(
    "cac cua cho voi trong tren duoi nay day duoc la va de khi thi neu o tai mot nhung "
    "nhu theo sau hay roi ma cung da se dang ra vao len lai thay co hoac bang dism".split()
)
_ASCII_SYLLABLES = frozenset(
    "theo sau cho hay tin nhu van can con lam xem tim cai noi ban ten vao the khi nao sao "
    "mai thi neu nen roi hinh anh mau khung nut chon nhan bam tiep tuc mot hai ba".split()
)
_GENERIC_PAIRS = frozenset(
    {
        "ung dung",
        "phan mem",
        "cua so",
        "giao dien",
        "hop thoai",
        "danh sach",
        "thu muc",
        "hien thi",
        "mau do",
        "mui ten",
        "khoanh vien",
        "vien mau",
        "khung mau",
        "ben phai",
        "ben trai",
        "goc duoi",
        "tep tin",
        "hinh anh",
        "hinh chup",
        "nguoi dung",
        "thong bao",
        "noi dung",
        "tu dong",
        "mo ta",
        "nhan chon",
        "bam chon",
        "tiep tuc",
        "bam vao",
        "nhan vao",
        "dang duoc",
        "duoc khoanh",
        "duoc chon",
        "may tinh",
        "he thong",
        "huong dan",
        "su dung",
        "sao luu",
        "ban quyen",
        "tai lieu",
        "thuc hien",
        "cong cu",
        "thoai thong",
    }
)

# (alt, key, source text just before the image) in source order.
ImageRefs = list[tuple[str, str, str]]


MARKER = re.compile(r"\[\s*(Hình\s*\d+)\s*\]", re.IGNORECASE)
_LABEL = re.compile(r"^\s*(Hình\s*\d+)", re.IGNORECASE)
_STEP_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
MIN_STEPS_FOR_FALLBACK = 3


def _label_key(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().lower())


class ImageMarkers:
    """Resolves the model's ``[Hình N]`` markers against the evidence images."""

    def __init__(self, evidence: list[tuple[str, str]]) -> None:
        """``evidence``: (evidence_id, content) in bundle order."""
        self._by_label: dict[str, list[tuple[str, str, str]]] = {}
        for evidence_id, content in evidence:
            for alt, key, _ in refs_in(content):
                if m := _LABEL.match(alt):
                    self._by_label.setdefault(_label_key(m.group(1)), []).append(
                        (evidence_id, alt, key)
                    )
        self.shown: set[str] = set()

    def resolve(self, label: str, cited_ids: list[str]) -> str:
        """The image line for a marker, or "" when unknown or already shown."""
        options = self._by_label.get(_label_key(label), [])
        rank = {eid: i for i, eid in enumerate(reversed(cited_ids))}
        for _, alt, key in sorted(options, key=lambda o: rank.get(o[0], len(rank))):
            if key not in self.shown and len(self.shown) < MAX_IMAGES:
                self.shown.add(key)
                return image_ref(alt, key)
        return ""

    def replace(self, text: str, cited_ids: list[str]) -> str:
        """``text`` with every marker turned into its image line (or removed)."""

        def swap(m: re.Match[str]) -> str:
            line = self.resolve(m.group(1), cited_ids)
            if not line:
                return ""
            before = "" if m.start() == 0 or text[m.start() - 1] == "\n" else "\n"
            after = "" if text[m.end() : m.end() + 1] in ("\n", "") else "\n"
            return f"{before}{line}{after}"

        out = MARKER.sub(swap, text)
        out = re.sub(r"[ \t]+\n", "\n", out)
        return re.sub(r"\n{3,}", "\n\n", out).strip()


def is_step_by_step(text: str) -> bool:
    return sum(1 for line in text.split("\n") if _STEP_LINE.match(line)) >= MIN_STEPS_FOR_FALLBACK


def _last_sentence(text: str) -> str:
    """The sentence right before an image ("Tiếp tục chọn Cancel."), not the intro above it."""
    sentences = [s for s in _SENTENCE.split(text.strip()) if s.strip()]
    return sentences[-1][-CONTEXT_CHARS:] if sentences else ""


def refs_in(text: str) -> ImageRefs:
    refs: ImageRefs = []
    last = 0
    for m in IMAGE_REF.finditer(text):
        refs.append((m.group("alt"), m.group("key"), _last_sentence(text[last : m.start()])))
        last = m.end()
    return refs


def _words(text: str) -> set[str]:
    tokens = _WORD.findall(IMAGE_REF.sub("", text).lower())
    plain = [unaccent_vietnamese(t) for t in tokens]
    features = {
        p
        for t, p in zip(tokens, plain, strict=True)
        if t == p and len(p) > 2 and not p.isdigit() and p not in _ASCII_SYLLABLES | _STOP
    }
    features.update(
        f"{a} {b}"
        for a, b in zip(plain, plain[1:], strict=False)
        if a not in _STOP and b not in _STOP and f"{a} {b}" not in _GENERIC_PAIRS
    )
    return features


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
        citing = [j for j, line in enumerate(lines) if f"[{number}]" in line]
        if not images or not citing:
            continue
        # Steps the model left uncited still belong to the evidence around them:
        # every line from the first citing one to the end of the last one's block.
        end = citing[-1]
        while end + 1 < len(lines) and lines[end + 1].strip():
            end += 1
        candidates = [j for j in range(citing[0], end + 1) if lines[j].strip()]
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
                [
                    ALT_WEIGHT * len(lw & alt_words) + min(len(lw & context_words), CONTEXT_CAP)
                    for lw in line_words
                ]
                for alt_words, context_words in described
            ]
        )
        # An unmatched image between matches on two different lines is a step
        # in between and goes with the next one; anywhere else it is about
        # something the answer does not cover, and is left out.
        matched = [i for i, slot in enumerate(assigned) if slot is not None]
        if not matched:
            continue
        for i in range(matched[-1] - 1, matched[0], -1):
            before = next(assigned[k] for k in range(i - 1, -1, -1) if assigned[k] is not None)
            if assigned[i] is None and before != assigned[i + 1]:
                assigned[i] = assigned[i + 1]
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
