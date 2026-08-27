"""Unit tests for the idempotency decision state machine (spec P9A-3)."""

from app.domain.models.documents import IngestDecision, StoredSourceState
from app.services.ingestion.versioning import decide_ingest_action


class TestDecideIngestAction:
    def test_no_previous_state_is_new(self) -> None:
        assert decide_ingest_action(None, "f" * 64) == IngestDecision.NEW

    def test_same_fingerprint_active_is_unchanged(self) -> None:
        previous = StoredSourceState(source_id="s", fingerprint="f" * 64)
        assert decide_ingest_action(previous, "f" * 64) == IngestDecision.UNCHANGED

    def test_different_fingerprint_is_modified(self) -> None:
        previous = StoredSourceState(source_id="s", fingerprint="a" * 64)
        assert decide_ingest_action(previous, "b" * 64) == IngestDecision.MODIFIED

    def test_deleted_source_reports_deleted_even_if_unchanged(self) -> None:
        previous = StoredSourceState(source_id="s", fingerprint="f" * 64, deleted=True)
        assert decide_ingest_action(previous, "f" * 64) == IngestDecision.DELETED

    def test_deleted_source_reports_deleted_even_if_modified(self) -> None:
        previous = StoredSourceState(source_id="s", fingerprint="a" * 64, deleted=True)
        assert decide_ingest_action(previous, "b" * 64) == IngestDecision.DELETED

    def test_failed_status_with_same_fingerprint_retries(self) -> None:
        previous = StoredSourceState(source_id="s", fingerprint="f" * 64, status="failed")
        decision = decide_ingest_action(previous, "f" * 64)
        assert decision == IngestDecision.RETRY_AFTER_FAILURE

    def test_failed_status_with_new_fingerprint_is_modified(self) -> None:
        previous = StoredSourceState(source_id="s", fingerprint="a" * 64, status="failed")
        assert decide_ingest_action(previous, "b" * 64) == IngestDecision.MODIFIED
