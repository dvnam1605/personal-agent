"""Rule-first document category classifier.

Scores every category from three signal kinds — a title line in the header
(strongest), filename tokens, and body markers — and reports a confidence
derived from how clearly the winner beats the runner-up. There is no
default category: weak evidence yields ``OTHER`` with low confidence, which
is what triggers the LLM fallback upstream.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.ingestion.profiling.categories import CATEGORY_SPECS, DocCategory
from app.services.ingestion.profiling.signals import DocumentSignals

TITLE_SCAN_LINES = 25
TITLE_MAX_WORDS = 14
W_TITLE = 3.0
W_FILENAME = 1.5
W_MARKER = 0.7
MAX_MARKERS = 3
MIN_WINNING_SCORE = 1.4


@dataclass(frozen=True)
class Classification:
    category: DocCategory
    subtype: str | None
    confidence: float
    title_line_index: int | None
    scores: dict[str, float]


def _title_candidates(line: str) -> list[str]:
    """The line itself plus any markdown heading merged into it.

    Converted sources often glue the place/date line and the title into one
    line ("Hà Nội, ngày … # QUYẾT ĐỊNH về việc …"); the title after ``#``
    still counts, but only short title-like segments do.
    """
    candidates = [line, *(seg.strip() for seg in line.split("#")[1:] if seg.strip())]
    return [c for c in candidates if c and len(c.split()) <= TITLE_MAX_WORDS] or [
        c for c in candidates[1:] if c
    ]


class RuleClassifier:
    """Deterministic classifier over :class:`DocumentSignals`."""

    def classify(self, signals: DocumentSignals) -> Classification:
        header = signals.header_folded.split("\n")[:TITLE_SCAN_LINES]
        markers_text = f"{signals.header_folded}\n{signals.tail_folded}"
        scores: dict[DocCategory, float] = {}
        subtypes: dict[DocCategory, tuple[str, int]] = {}

        for spec in CATEGORY_SPECS:
            score = 0.0
            for idx, line in enumerate(header):
                hit = next(
                    (
                        s
                        for candidate in _title_candidates(line)
                        for s in spec.subtypes
                        if s.pattern.search(candidate)
                    ),
                    None,
                )
                if hit:
                    # Earlier title lines are more likely the real document title.
                    score += W_TITLE * (1.0 - 0.02 * idx)
                    subtypes[spec.category] = (hit.label, idx)
                    break
            if spec.filename_pattern and spec.filename_pattern.search(signals.filename_folded):
                score += W_FILENAME
            hits = sum(1 for m in spec.markers if m.search(markers_text))
            score += W_MARKER * min(hits, MAX_MARKERS)
            scores[spec.category] = round(score, 3)

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best, best_score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        if best_score < MIN_WINNING_SCORE:
            return Classification(
                DocCategory.OTHER, None, 0.2, None, {k.value: v for k, v in scores.items()}
            )

        margin = (best_score - runner_up) / best_score
        strength = min(1.0, best_score / (W_TITLE + W_MARKER * 2))
        confidence = round(max(0.05, min(1.0, 0.35 + 0.65 * margin * strength)), 3)
        subtype, title_idx = subtypes.get(best, (None, None))
        return Classification(
            best, subtype, confidence, title_idx, {k.value: v for k, v in scores.items()}
        )
