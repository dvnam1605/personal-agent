"""Pure fingerprint computation for ingestion idempotency (spec P9A-3)."""

from __future__ import annotations

import hashlib
import json

from app.domain.models.documents import DocumentFingerprint, FingerprintInputs

_FINGERPRINT_VERSION = "fp_v1"


def compute_fingerprint(inputs: FingerprintInputs) -> DocumentFingerprint:
    """Hash the canonical JSON of every identity-relevant input.

    Pure function: same inputs always yield the same 64-hex fingerprint; any
    input change (including a component version bump) yields a new one.
    """
    payload = {
        "version": _FINGERPRINT_VERSION,
        "source_id": inputs.source_id,
        "checksum": inputs.checksum,
        "modified_at": inputs.modified_at.isoformat() if inputs.modified_at else None,
        "size_bytes": inputs.size_bytes,
        "parser_version": inputs.parser_version,
        "parent_chunker_version": inputs.parent_chunker_version,
        "child_chunker_version": inputs.child_chunker_version,
        "embedding_model": inputs.embedding_model,
        "embedding_dimensions": inputs.embedding_dimensions,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return DocumentFingerprint(fingerprint=digest, inputs=inputs)
