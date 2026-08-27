"""Pre-rerank candidate hygiene: dedup, near-duplicate suppression, caps (P10-06).

Deterministic and cheap on purpose — no embeddings, no LLM:

- duplicate ``chunk_id`` entries collapse to their first occurrence;
- children whose normalised text repeats an earlier candidate are suppressed
  (the first/better-ranked instance wins), which stops adjacent near-identical
  chunks from dominating the reranker input;
- optional per-document candidate caps apply in arrival (fusion) order, giving
  every document fair candidate opportunity; COMPARE_DOCUMENTS gets a derived
  balance cap. Detecting *missing* sources belongs to the P10C compare policy
  and is intentionally NOT attempted here.
"""

from __future__ import annotations

import math
import re

from app.domain.models.retrieval import RetrievedChunk

_WS_RE = re.compile(r"\s+")


def _normalised_text(chunk: RetrievedChunk) -> str:
    return _WS_RE.sub(" ", chunk.content_raw).strip().lower()


def dedup_candidates(candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Collapse repeated chunk_ids, keeping the better-ranked first copy."""
    seen: set[str] = set()
    kept: list[RetrievedChunk] = []
    for chunk in candidates:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        kept.append(chunk)
    return kept


def suppress_near_duplicates(candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Drop later candidates whose normalised body repeats an earlier one."""
    seen_texts: set[str] = set()
    kept: list[RetrievedChunk] = []
    for chunk in candidates:
        key = _normalised_text(chunk)
        if key and key in seen_texts:
            continue
        if key:
            seen_texts.add(key)
        kept.append(chunk)
    return kept


def apply_per_document_caps(
    candidates: list[RetrievedChunk], cap: int | None
) -> list[RetrievedChunk]:
    """Trim per-document dominance in arrival order (cap=None disables)."""
    if cap is None or cap < 1:
        return candidates
    counts: dict[str, int] = {}
    kept: list[RetrievedChunk] = []
    for chunk in candidates:
        used = counts.get(chunk.document_id, 0)
        if used >= cap:
            continue
        counts[chunk.document_id] = used + 1
        kept.append(chunk)
    return kept


def balanced_cap_for_compare(
    candidates: list[RetrievedChunk], document_ids: list[str]
) -> int | None:
    """Opportunity cap so requested compare sources share the fused input."""
    requested = max(len(document_ids), 1)
    if not candidates:
        return None
    return max(1, math.ceil(len(candidates) / requested))


def apply_diversity(
    candidates: list[RetrievedChunk],
    *,
    per_document_cap: int | None = None,
    compare_document_ids: list[str] | None = None,
) -> list[RetrievedChunk]:
    """Full P10-06 chain: dedup -> near-dup suppression -> caps."""
    filtered = suppress_near_duplicates(dedup_candidates(candidates))
    cap = per_document_cap
    if compare_document_ids:
        derived = balanced_cap_for_compare(filtered, compare_document_ids)
        if derived is not None:
            cap = min(cap, derived) if cap is not None else derived
    return apply_per_document_caps(filtered, cap)
