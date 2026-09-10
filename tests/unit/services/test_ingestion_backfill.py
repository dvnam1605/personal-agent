"""Unit tests for FingerprintBackfillJob (spec P17, P16 M4 backlog)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.documents import (
    FingerprintInputs,
    SourceDocument,
)
from app.domain.models.ingestion import IngestionObservability, IngestionStatus
from app.services.ingestion.backfill import FingerprintBackfillJob
from app.services.ingestion.fingerprint import compute_legacy_fingerprint_v1


@pytest.mark.asyncio
async def test_backfill_job_upgrades_legacy_v1_documents() -> None:
    source_v1 = SourceDocument(
        source_id="doc_v1",
        source_type="upload",
        filename="doc_v1.md",
        mime_type="text/markdown",
        checksum="aa" * 32,
        modified_at=datetime(2026, 8, 20, tzinfo=UTC),
        size_bytes=32,
    )
    content_v1 = b"# Legacy Content\n\nSome text."

    source_v2 = SourceDocument(
        source_id="doc_v2",
        source_type="upload",
        filename="doc_v2.md",
        mime_type="text/markdown",
        checksum="bb" * 32,
        modified_at=datetime(2026, 8, 20, tzinfo=UTC),
        size_bytes=32,
    )
    content_v2 = b"# Modern Content\n\nSome text."

    mock_orchestrator = MagicMock()

    state_v1 = MagicMock()
    state_v2 = MagicMock()

    async def mock_check_legacy(src: SourceDocument) -> tuple[bool, Any]:
        if src.source_id == "doc_v1":
            return True, state_v1
        return False, state_v2

    mock_orchestrator.check_legacy_fingerprint = AsyncMock(side_effect=mock_check_legacy)

    obs_success = IngestionObservability(
        job_id="job_123",
        document_id="doc_v1_v2",
        source_id="doc_v1",
        status=IngestionStatus.COMPLETED,
        document_version=2,
    )
    mock_orchestrator.ingest_source = AsyncMock(return_value=obs_success)

    job = FingerprintBackfillJob(
        orchestrator=mock_orchestrator,
        batch_size=5,
        throttle_delay_seconds=0.0,
    )

    progress = await job.run([(source_v1, content_v1), (source_v2, content_v2)])

    assert progress.scanned == 2
    # doc_v1 had legacy fp_v1 -> upgraded!
    assert progress.upgraded == 1
    # doc_v2 already had fp_v2 -> skipped
    assert progress.skipped == 1
    assert progress.errors == []
    # Assert force_reindex=True was passed to ingest_source for the legacy document
    mock_orchestrator.ingest_source.assert_awaited_once_with(
        source_v1, content_v1, force_reindex=True
    )


@pytest.mark.asyncio
async def test_orchestrator_check_legacy_fingerprint_uses_hashed_logical_document_id() -> None:
    from app.services.ingestion.orchestrator import IngestionOrchestrator, logical_document_id_for

    mock_repo = MagicMock()
    mock_tx = MagicMock()
    mock_embed = MagicMock()

    source = SourceDocument(
        source_id="extremely_long_source_id_exceeding_standard_limits_1234567890",
        source_type="upload",
        filename="test.md",
    )
    expected_logical_id = logical_document_id_for(source.source_id)
    assert expected_logical_id.startswith("ldg-")
    assert len(expected_logical_id) <= 36

    fp_inputs = FingerprintInputs(
        source_id=source.source_id,
        checksum="cc" * 32,
        parser_version="p1",
        parent_chunker_version="c1",
        child_chunker_version="c2",
        embedding_model="m",
        embedding_dimensions=1024,
    )

    legacy_hash = compute_legacy_fingerprint_v1(fp_inputs)
    mock_state = MagicMock()
    mock_state.fingerprint = legacy_hash

    # Repo must be queried using expected_logical_id, NOT raw source_id!
    async def mock_latest_state(lid: str) -> Any:
        if lid == expected_logical_id:
            return mock_state
        return None

    mock_repo.latest_state = AsyncMock(side_effect=mock_latest_state)

    orchestrator = IngestionOrchestrator(
        repository=mock_repo,
        transaction=mock_tx,
        embedding=mock_embed,
    )
    orchestrator._fingerprint_inputs = MagicMock(return_value=fp_inputs)  # type: ignore

    is_legacy, state = await orchestrator.check_legacy_fingerprint(source)
    assert is_legacy is True
    assert state == mock_state
    mock_repo.latest_state.assert_awaited_once_with(expected_logical_id)
