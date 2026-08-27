"""Unit tests for ingestion fingerprint computation (spec P9A-3)."""

from datetime import UTC, datetime

from app.domain.models.documents import FingerprintInputs
from app.services.ingestion.fingerprint import compute_fingerprint


def _inputs(**overrides: object) -> FingerprintInputs:
    base: dict[str, object] = {
        "source_id": "src-1",
        "checksum": "ab" * 32,
        "modified_at": datetime(2026, 8, 22, tzinfo=UTC),
        "size_bytes": 2048,
        "parser_version": "docling-1",
        "parent_chunker_version": "pc-1",
        "child_chunker_version": "cc-1",
        "embedding_model": "AITeamVN/Vietnamese_Embedding",
        "embedding_dimensions": 1024,
    }
    base.update(overrides)
    return FingerprintInputs.model_validate(base)


class TestFingerprintStability:
    def test_same_inputs_same_fingerprint(self) -> None:
        first = compute_fingerprint(_inputs())
        second = compute_fingerprint(_inputs())
        assert first.fingerprint == second.fingerprint
        assert len(first.fingerprint) == 64

    def test_fingerprint_is_hex(self) -> None:
        result = compute_fingerprint(_inputs())
        int(result.fingerprint, 16)

    def test_inputs_round_tripped_on_result(self) -> None:
        inputs = _inputs()
        assert compute_fingerprint(inputs).inputs == inputs


class TestFingerprintSensitivity:
    def test_checksum_change_changes_fingerprint(self) -> None:
        base = compute_fingerprint(_inputs()).fingerprint
        assert compute_fingerprint(_inputs(checksum="cd" * 32)).fingerprint != base

    def test_modified_at_change_changes_fingerprint(self) -> None:
        base = compute_fingerprint(_inputs()).fingerprint
        changed = compute_fingerprint(
            _inputs(modified_at=datetime(2026, 8, 23, tzinfo=UTC))
        ).fingerprint
        assert changed != base

    def test_size_change_changes_fingerprint(self) -> None:
        base = compute_fingerprint(_inputs()).fingerprint
        assert compute_fingerprint(_inputs(size_bytes=4096)).fingerprint != base

    def test_parser_version_bump_changes_fingerprint(self) -> None:
        base = compute_fingerprint(_inputs()).fingerprint
        assert compute_fingerprint(_inputs(parser_version="docling-2")).fingerprint != base

    def test_parent_chunker_version_bump_changes_fingerprint(self) -> None:
        base = compute_fingerprint(_inputs()).fingerprint
        changed = compute_fingerprint(_inputs(parent_chunker_version="pc-2")).fingerprint
        assert changed != base

    def test_child_chunker_version_bump_changes_fingerprint(self) -> None:
        base = compute_fingerprint(_inputs()).fingerprint
        changed = compute_fingerprint(_inputs(child_chunker_version="cc-2")).fingerprint
        assert changed != base

    def test_embedding_model_change_changes_fingerprint(self) -> None:
        base = compute_fingerprint(_inputs()).fingerprint
        changed = compute_fingerprint(
            _inputs(embedding_model="other-model", embedding_dimensions=768)
        ).fingerprint
        assert changed != base

    def test_source_id_change_changes_fingerprint(self) -> None:
        base = compute_fingerprint(_inputs()).fingerprint
        assert compute_fingerprint(_inputs(source_id="src-2")).fingerprint != base

    def test_none_vs_value_differ(self) -> None:
        with_value = compute_fingerprint(_inputs(checksum=None)).fingerprint
        without_value = compute_fingerprint(
            _inputs(checksum=None, modified_at=None, size_bytes=None)
        ).fingerprint
        assert with_value != without_value
