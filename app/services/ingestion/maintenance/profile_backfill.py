"""Metadata-only backfill: (re)profile stored documents without re-embedding.

Text is rebuilt from each version's parent chunks, so neither the source
bytes nor the embedding model are needed, and chunk embeddings are never
touched. Manual overrides already on a profile are re-applied.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.infrastructure.db.profile_repository import SqlAlchemyProfileRepository
from app.services.ingestion.profiling import EXTRACTOR_VERSION, DocumentProfiler
from app.services.ingestion.profiling.profiler import DocumentProfileDraft

logger = logging.getLogger(__name__)


@dataclass
class BackfillReport:
    profiled: int = 0
    skipped_empty: int = 0
    needs_review: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    drafts: list[tuple[str, DocumentProfileDraft]] = field(default_factory=list)


class ProfileBackfillJob:
    def __init__(self, store: SqlAlchemyProfileRepository, profiler: DocumentProfiler) -> None:
        self._store = store
        self._profiler = profiler

    async def run(self, *, stale_only: bool = True, dry_run: bool = False) -> BackfillReport:
        report = BackfillReport()
        targets = await self._store.targets(
            stale_only=stale_only, extractor_version=EXTRACTOR_VERSION
        )
        for target in targets:
            text, headings = await self._store.document_text(target.document_id)
            if not text.strip():
                report.skipped_empty += 1
                continue
            draft = await self._profiler.aprofile_text(
                text, target.filename, headings=headings, overrides=target.overrides or None
            )
            report.profiled += 1
            report.needs_review += int(draft.needs_review)
            cat = draft.doc_category.value
            report.by_category[cat] = report.by_category.get(cat, 0) + 1
            report.drafts.append((target.filename, draft))
            if not dry_run:
                await self._store.save_standalone(
                    document_id=target.document_id,
                    logical_document_id=target.logical_document_id,
                    draft=draft,
                    overrides=target.overrides,
                )
            logger.info(
                "profile_backfilled",
                extra={"document_id": target.document_id, "category": cat},
            )
        return report
