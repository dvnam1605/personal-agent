"""Context packing under an explicit token budget (spec P10-13).

Greedy fill in caller-priority order; the hard max is respected exactly —
overflow whole units are DROPPED with a warning, never truncated mid-chunk
(rag strategy §7). ``EvidenceBundle.retrieval_trace_id`` gets a fresh uuid4
hex unless the caller supplies one.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.domain.models.retrieval import Evidence, EvidenceBundle, EvidenceUnitKind, RetrievedChunk
from app.services.ingestion.chunking.tokens import estimate_tokens

logger = logging.getLogger(__name__)

# Internal routing keys stripped from citation-facing anchor payloads.
_INTERNAL_META_KEYS = frozenset({"node_type", "heading_path", "retrieval_sources"})


def citation_anchors(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep only citation-facing metadata keys on evidence items."""
    return {key: value for key, value in metadata.items() if key not in _INTERNAL_META_KEYS}


def unit_for_chunk(
    chunk: RetrievedChunk,
    *,
    kind: EvidenceUnitKind | None = None,
) -> Evidence:
    """Wrap a standalone CHILD/TABLE_CHILD hit into a packable unit."""
    resolved_kind: EvidenceUnitKind = kind or (
        "TABLE_CHILD" if chunk.metadata.get("node_type") == "TABLE_CHILD" else "CHILD"
    )
    headings = [str(item) for item in chunk.metadata.get("heading_path", [])]
    return Evidence(
        kind=resolved_kind,
        content_raw=chunk.content_raw,
        token_estimate=estimate_tokens(chunk.content_raw),
        primary_chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        parent_id=chunk.parent_id,
        chunk_ids=[chunk.chunk_id],
        heading_path=headings,
        anchors=citation_anchors(chunk.metadata),
    )


def build_bundle(
    units: list[Evidence],
    *,
    token_budget: int,
    trace_id: str | None = None,
) -> EvidenceBundle:
    """Greedy fill; drop-with-warning overflow preserves byte-exact hard max."""
    packed: list[Evidence] = []
    total_tokens = 0
    dropped = 0
    for unit in units:
        if total_tokens + unit.token_estimate <= token_budget:
            packed.append(unit)
            total_tokens += unit.token_estimate
        else:
            dropped += 1
            logger.warning(
                "context_pack_overflow",
                extra={
                    "primary_chunk_id": unit.primary_chunk_id,
                    "unit_kind": unit.kind,
                    "unit_tokens": unit.token_estimate,
                    "budget": token_budget,
                },
            )
    if dropped:
        logger.warning("context_pack_dropped_units", extra={"count": dropped})
    return EvidenceBundle(
        items=packed,
        total_tokens=total_tokens,
        documents_used=sorted({unit.document_id for unit in packed}),
        parent_ids_used=sorted({pid for unit in packed if (pid := unit.parent_id)}),
        retrieval_trace_id=trace_id or uuid.uuid4().hex,
    )
