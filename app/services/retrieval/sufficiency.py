"""Deterministic sufficiency rules (spec P10-16).

Cheap rules first, no LLM judge — the verdict drives the bounded retry logic
in P10-17.  Three-state output: SUFFICIENT / PARTIAL / INSUFFICIENT.

Default thresholds are documented baselines for P10D ablation tuning.
"""

from __future__ import annotations

import logging

from app.domain.models.retrieval import (
    Evidence,
    EvidenceBundle,
    RetrievalMode,
    RetrievalQuery,
)
from app.domain.models.sufficiency import SufficiencyStatus, SufficiencyVerdict

logger = logging.getLogger(__name__)

# Baseline threshold: 0.0 for V1 with IdentityReranker/RRF fusion (max RRF ~0.033).
# Threshold calibration for calibrated cross-encoder scores is ablated in P10D.
DEFAULT_MIN_SCORE_THRESHOLD = 0.0
DEFAULT_MIN_EVIDENCE_ITEMS = 1


class SufficiencyChecker:
    """Evaluate an ``EvidenceBundle`` against deterministic sufficiency rules."""

    def __init__(
        self,
        *,
        min_score_threshold: float = DEFAULT_MIN_SCORE_THRESHOLD,
        min_evidence_items: int = DEFAULT_MIN_EVIDENCE_ITEMS,
    ) -> None:
        self._min_score = min_score_threshold
        self._min_items = min_evidence_items

    def check(
        self,
        bundle: EvidenceBundle,
        query: RetrievalQuery,
    ) -> SufficiencyVerdict:
        """Run rules in priority order; first failure determines the verdict."""
        # Rule 1: zero candidates → INSUFFICIENT
        if not bundle.items:
            return SufficiencyVerdict(
                status=SufficiencyStatus.INSUFFICIENT,
                reason="No evidence items in bundle",
                retry_hint="broaden search terms or relax filters",
            )

        # Rule 2: below minimum item count → INSUFFICIENT
        if len(bundle.items) < self._min_items:
            return SufficiencyVerdict(
                status=SufficiencyStatus.INSUFFICIENT,
                reason=f"Only {len(bundle.items)} item(s), need at least {self._min_items}",
                retry_hint="increase candidate pool size",
            )

        # Rule 3: compare mode — requested doc missing → PARTIAL
        if query.mode is RetrievalMode.COMPARE_DOCUMENTS and query.document_ids:
            covered = set(bundle.documents_used)
            missing = [did for did in query.document_ids if did not in covered]
            if missing:
                return SufficiencyVerdict(
                    status=SufficiencyStatus.PARTIAL,
                    missing_document_ids=missing,
                    reason=f"Compare mode missing evidence from {len(missing)} document(s)",
                    retry_hint="relax filters or increase per-document budget",
                )

        # Rule 4: document-search mode — requested doc not in bundle → PARTIAL
        if query.mode is RetrievalMode.DOCUMENT_SEARCH and query.document_ids:
            covered = set(bundle.documents_used)
            missing = [did for did in query.document_ids if did not in covered]
            if missing:
                return SufficiencyVerdict(
                    status=SufficiencyStatus.PARTIAL,
                    missing_document_ids=missing,
                    reason="Requested document(s) missing from evidence",
                )

        # Rule 5: all scores below threshold → INSUFFICIENT
        if self._all_below_threshold(bundle):
            return SufficiencyVerdict(
                status=SufficiencyStatus.INSUFFICIENT,
                reason=f"All evidence scores below threshold {self._min_score}",
                retry_hint="reformulate query for better semantic match",
            )

        return SufficiencyVerdict(
            status=SufficiencyStatus.SUFFICIENT,
            reason="Evidence meets all sufficiency criteria",
        )

    def _all_below_threshold(self, bundle: EvidenceBundle) -> bool:
        """True when every item contains a score below the threshold.

        When min_score_threshold <= 0.0, score filtering is disabled (for V1
        baseline with IdentityReranker where RRF scores are <= 0.033).
        Falls back to False (treat as sufficient) when scores are unavailable.
        """
        if self._min_score <= 0.0:
            return False

        scores: list[float] = []
        for item in bundle.items:
            score = _extract_evidence_score(item)
            if score is not None:
                scores.append(score)
        if not scores:
            return False
        return all(s < self._min_score for s in scores)


def _extract_evidence_score(item: Evidence) -> float | None:
    """Extract score from Evidence fields, falling back to anchor metadata."""
    if item.rerank_score is not None:
        return float(item.rerank_score)
    if item.score is not None:
        return float(item.score)
    for key in ("rerank_score", "fusion_score", "score"):
        val = item.anchors.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None
