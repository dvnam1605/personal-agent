"""Compare-document policy enforcement (spec P10-14).

For ``COMPARE_DOCUMENTS`` mode: validates that the evidence bundle covers every
requested document and reports any gaps.  Works with the P10B diversity layer
(``apply_diversity`` already enforces per-document caps); this module adds the
post-packing coverage audit.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.retrieval import EvidenceBundle

logger = logging.getLogger(__name__)


class CompareResult(BaseModel):
    """Outcome of compare-document coverage audit."""

    model_config = ConfigDict(frozen=True)

    covered_documents: list[str] = Field(default_factory=list)
    missing_documents: list[str] = Field(default_factory=list)
    balance_ratio: float = 1.0


def enforce_compare_diversity(
    bundle: EvidenceBundle,
    document_ids: list[str],
) -> CompareResult:
    """Audit ``bundle`` for per-document coverage against requested ids.

    Returns a ``CompareResult`` with the list of covered/missing documents and a
    balance ratio (least-represented / most-represented count, 1.0 = perfect).
    """
    if not document_ids:
        return CompareResult(
            covered_documents=sorted(bundle.documents_used),
            balance_ratio=1.0,
        )

    doc_counts: dict[str, int] = dict.fromkeys(document_ids, 0)
    for item in bundle.items:
        if item.document_id in doc_counts:
            doc_counts[item.document_id] += 1

    covered = sorted(did for did, count in doc_counts.items() if count > 0)
    missing = sorted(did for did, count in doc_counts.items() if count == 0)

    counts = [c for c in doc_counts.values() if c > 0]
    if len(counts) >= 2:
        balance = min(counts) / max(counts)
    elif len(counts) == 1:
        balance = 0.0 if missing else 1.0
    else:
        balance = 0.0

    if missing:
        logger.warning(
            "compare_document_missing_evidence",
            extra={"missing_document_ids": missing, "covered": covered},
        )

    return CompareResult(
        covered_documents=covered,
        missing_documents=missing,
        balance_ratio=round(balance, 4),
    )
