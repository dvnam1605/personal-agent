"""Deterministic chunk identities (spec P9C-3/P9C-4).

IDs are content-derived so re-chunking an unchanged document version yields
identical IDs (reproducible diffs); changing any ingredient changes the ID.
Random UUIDs are never used here.
"""

from __future__ import annotations

import hashlib
import json

# 1.1.0: token estimator recalibrated with structural pricing (P9C review M1).
# Version bumps deliberately force reindexing of anything chunked before.
PARENT_CHUNKER_VERSION = "p9c-parent-1.1.0"
CHILD_CHUNKER_VERSION = "p9c-child-1.1.0"


def _canonical_digest(payload: list[object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def parent_chunk_id(
    *,
    document_version_id: str,
    heading_path: tuple[str, ...],
    block_range: tuple[str | None, str | None],
    ordinal: int,
    version: str = PARENT_CHUNKER_VERSION,
) -> str:
    """Stable parent identity within one document version."""
    digest = _canonical_digest(
        [
            "parent",
            document_version_id,
            list(heading_path),
            block_range[0],
            block_range[1],
            ordinal,
            version,
        ]
    )
    return f"par-{digest[:32]}"


def child_chunk_id(
    *,
    parent_id: str,
    block_range: tuple[str | None, str | None],
    ordinal: int,
    content_hash: str,
    version: str = CHILD_CHUNKER_VERSION,
) -> str:
    """Stable child identity; references exactly one parent in V1."""
    digest = _canonical_digest(
        [
            "child",
            parent_id,
            block_range[0],
            block_range[1],
            ordinal,
            content_hash,
            version,
        ]
    )
    return f"chl-{digest[:32]}"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
