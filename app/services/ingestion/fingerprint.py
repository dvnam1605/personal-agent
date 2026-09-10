"""Pure fingerprint computation for ingestion idempotency (spec P9A-3)."""

from __future__ import annotations

import hashlib
import json

from app.domain.models.ingestion.documents import DocumentFingerprint, FingerprintInputs

_FINGERPRINT_VERSION = "fp_v2"
_FINGERPRINT_VERSION_V1 = "fp_v1"


def _digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload_v1(inputs: FingerprintInputs) -> dict[str, object]:
    """Pre-chunking-field identity hash (fp_v1). Used only for skip-compat."""
    return {
        "version": _FINGERPRINT_VERSION_V1,
        "source_id": inputs.source_id,
        "checksum": inputs.checksum,
        "modified_at": inputs.modified_at.isoformat() if inputs.modified_at else None,
        "size_bytes": inputs.size_bytes,
        "parser_version": inputs.parser_version,
        "parent_chunker_version": inputs.parent_chunker_version,
        "child_chunker_version": inputs.child_chunker_version,
        "embedding_model": inputs.embedding_model,
        "embedding_dimensions": inputs.embedding_dimensions,
        "ocr_source_checksum": inputs.ocr_source_checksum,
        "ocr_engine": inputs.ocr_engine,
        "ocr_engine_version": inputs.ocr_engine_version,
        "ocr_device": inputs.ocr_device,
    }


def _payload_v2(inputs: FingerprintInputs) -> dict[str, object]:
    payload = _payload_v1(inputs)
    payload["version"] = _FINGERPRINT_VERSION
    payload["parent_target_tokens"] = inputs.parent_target_tokens
    payload["child_target_tokens"] = inputs.child_target_tokens
    payload["parent_hard_max_tokens"] = inputs.parent_hard_max_tokens
    payload["child_hard_max_tokens"] = inputs.child_hard_max_tokens
    return payload


def compute_fingerprint(inputs: FingerprintInputs) -> DocumentFingerprint:
    """Hash the canonical JSON of every identity-relevant input.

    Pure function: same inputs always yield the same 64-hex fingerprint; any
    input change (including a component version bump) yields a new one.
    New writes always emit fp_v2.
    """
    return DocumentFingerprint(fingerprint=_digest(_payload_v2(inputs)), inputs=inputs)


def compute_legacy_fingerprint_v1(inputs: FingerprintInputs) -> str:
    """fp_v1 digest used to skip a one-shot reindex of the existing corpus."""
    return _digest(_payload_v1(inputs))


def fingerprint_matches_stored(stored: str | None, inputs: FingerprintInputs) -> tuple[bool, bool]:
    """Return ``(unchanged, is_legacy_v1)`` for skip/throttle decisions.

    Legacy rows with ``fingerprint is None`` never match; the caller treats
    them as MODIFIED so they reingest instead of colliding at version 1.
    """
    if not stored:
        return False, False
    current = compute_fingerprint(inputs).fingerprint
    if stored == current:
        return True, False
    if stored == compute_legacy_fingerprint_v1(inputs):
        return True, True
    return False, False
