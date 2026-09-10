"""Idempotency decision logic over stored and current fingerprints (spec P9A-3)."""

from __future__ import annotations

from app.domain.models.ingestion.documents import IngestDecision, StoredSourceState


def decide_ingest_action(
    previous: StoredSourceState | None,
    current_fingerprint: str,
) -> IngestDecision:
    """Map (previous state, current fingerprint) to a pipeline decision.

    Transitions:

    - no previous state            -> NEW
    - previous marked deleted      -> DELETED
    - same fingerprint, active     -> UNCHANGED
    - different fingerprint        -> MODIFIED
    - same fingerprint, failed     -> RETRY_AFTER_FAILURE
    """
    if previous is None:
        return IngestDecision.NEW
    if previous.deleted:
        return IngestDecision.DELETED
    if previous.fingerprint == current_fingerprint:
        if previous.status == "failed":
            return IngestDecision.RETRY_AFTER_FAILURE
        return IngestDecision.UNCHANGED
    return IngestDecision.MODIFIED
