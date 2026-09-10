"""FingerprintBackfillJob for controlled throttled upgrade of fp_v1 documents to fp_v2 (spec P17, backlog M4)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.domain.models.ingestion import IngestionStatus
from app.domain.models.ingestion.documents import SourceDocument
from app.services.ingestion.orchestrator import IngestionOrchestrator

logger = structlog.get_logger(__name__)


@dataclass
class BackfillProgress:
    """Telemetry report for fingerprint backfill execution."""

    scanned: int = 0
    upgraded: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class FingerprintBackfillJob:
    """Throttled background job upgrading documents with legacy fp_v1 fingerprints to fp_v2."""

    def __init__(
        self,
        orchestrator: IngestionOrchestrator,
        batch_size: int = 10,
        throttle_delay_seconds: float = 0.01,
    ) -> None:
        self._orchestrator = orchestrator
        self._batch_size = batch_size
        self._delay = throttle_delay_seconds

    async def run(
        self,
        items: Sequence[tuple[SourceDocument, bytes]],
        progress_callback: Any | None = None,
    ) -> BackfillProgress:
        """Scan sources and upgrade legacy fp_v1 documents in controlled batches."""
        progress = BackfillProgress()

        for idx, (source, content) in enumerate(items):
            progress.scanned += 1
            try:
                is_legacy, state = await self._orchestrator.check_legacy_fingerprint(source)
                if is_legacy:
                    # Legacy v1 match detected! Ingest with force_reindex to apply modern chunking settings and emit fp_v2.
                    obs = await self._orchestrator.ingest_source(
                        source, content, force_reindex=True
                    )
                    if obs.status == IngestionStatus.COMPLETED:
                        progress.upgraded += 1
                        logger.info("backfill_upgraded_fp_v1_to_v2", source_id=source.source_id)
                    else:
                        progress.errors.append(f"{source.source_id}: {obs.status.value}")
                elif state is None:
                    # New document; standard ingest
                    obs = await self._orchestrator.ingest_source(source, content)
                    if obs.status == IngestionStatus.COMPLETED:
                        progress.upgraded += 1
                    else:
                        progress.errors.append(f"{source.source_id}: {obs.status.value}")
                else:
                    progress.skipped += 1

            except Exception as exc:  # noqa: BLE001 - per-source backfill isolation
                logger.exception("backfill_item_failed", source_id=source.source_id, error=str(exc))
                progress.errors.append(f"{source.source_id}: {exc}")

            if progress_callback is not None:
                progress_callback(progress)

            # Throttle between items / batches
            if (idx + 1) % self._batch_size == 0 and self._delay > 0:
                await asyncio.sleep(self._delay)

        return progress
