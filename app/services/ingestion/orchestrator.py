"""Ingestion orchestrator: parse -> chunk -> embed -> persist (spec P9D).

Wires P9B parsing and P9C chunking into the complete pipeline with typed job
statuses, per-stage durations, batch-safe failure isolation, and atomic
version activation. One failed file never raises past this layer; callers
inspect the returned observability record.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

import structlog

from app.core.config import ChunkingSettings, get_settings
from app.domain.models.ingestion import (
    IngestionObservability,
    IngestionStatus,
    StoredFingerprintState,
)
from app.domain.models.ingestion.chunks import ChunkLevel
from app.domain.models.ingestion.documents import FingerprintInputs, SourceDocument
from app.domain.models.ingestion.parsed_document import ParseStatus
from app.services.ingestion.chunking.children import SentenceChildChunker
from app.services.ingestion.chunking.identity import (
    CHILD_CHUNKER_VERSION,
    PARENT_CHUNKER_VERSION,
)
from app.services.ingestion.chunking.parents import SectionParentChunker
from app.services.ingestion.chunking.protocols import ChunkContext
from app.services.ingestion.embedding import LocalEmbeddingService
from app.services.ingestion.fingerprint import compute_fingerprint, fingerprint_matches_stored
from app.services.ingestion.parsing.base import parse_source
from app.services.ingestion.parsing.markdown_parser import MARKDOWN_PARSER_VERSION
from app.services.ingestion.persistence import IngestionRepository, UnitOfWork

logger = structlog.get_logger(__name__)

StatusCallback = Callable[[IngestionStatus], Awaitable[None]]


def logical_document_id_for(source_id: str) -> str:
    """Deterministic grouping key within the String(36) column limit."""
    return "ldg-" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:32]


def document_id_for(logical_document_id: str, version_number: int) -> str:
    """Deterministic candidate PK so chunk identity exists before insertion."""
    payload = f"{logical_document_id}#v{version_number}".encode()
    return "doc-" + hashlib.sha256(payload).hexdigest()[:32]


def _parser_version_for(source: SourceDocument) -> str:
    if source.source_type == "preparsed_markdown":
        return MARKDOWN_PARSER_VERSION
    from app.services.ingestion.parsing.docling_parser import docling_version

    return docling_version()


def _parser_name_for(source: SourceDocument) -> str:
    return "markdown" if source.source_type == "preparsed_markdown" else "docling"


class IngestionOrchestrator:
    """Runs one source through the full pipeline with typed status tracking."""

    def __init__(
        self,
        *,
        repository: IngestionRepository,
        transaction: UnitOfWork,
        embedding: LocalEmbeddingService,
        heartbeat_callback: Callable[[str], Awaitable[None]] | None = None,
        chunking_settings: ChunkingSettings | None = None,
    ) -> None:
        self._repository = repository
        self._transaction = transaction
        self._embedding = embedding
        self._heartbeat = heartbeat_callback
        self._chunking_settings = (chunking_settings or get_settings().chunking).model_copy()

    async def ingest_source(
        self,
        source: SourceDocument,
        content: bytes,
        *,
        job_id: str | None = None,
        on_status: StatusCallback | None = None,
        force_reindex: bool = False,
    ) -> IngestionObservability:
        obs = _Obs(job_id or str(uuid4()), source, len(content))
        started_stage = time.perf_counter()

        async def mark(status: IngestionStatus) -> None:
            nonlocal started_stage
            obs.status = status
            started_stage = time.perf_counter()
            if on_status is not None:
                await on_status(status)
            if self._heartbeat is not None:
                try:
                    await self._heartbeat(obs.job_id)
                except Exception as exc:  # noqa: BLE001 - heartbeat is best-effort
                    # Heartbeat is best-effort, but a dead heartbeat silently
                    # disables stale-claim protection — never swallow it quietly.
                    logger.debug(
                        "ingestion_heartbeat_failed",
                        extra={"job_id": obs.job_id, "error": str(exc)},
                    )

        def stage_elapsed() -> float:
            return round(time.perf_counter() - started_stage, 6)

        try:
            await mark(IngestionStatus.RUNNING)
            fingerprint = compute_fingerprint(self._fingerprint_inputs(source)).fingerprint
            logical_id = logical_document_id_for(source.source_id)

            # This read sits outside the persist transaction, so two concurrent
            # ingests can both elect the same N+1. The in-transaction check in
            # persist_candidate re-reads the latest row (FOR UPDATE on PG),
            # bumps to N+2 with remapped chunk IDs on collision, or returns
            # the existing version with is_deduplicated=True — no corruption,
            # no silent overwrite.
            state = await self._repository.latest_state(logical_id)
            unchanged, is_legacy = (
                fingerprint_matches_stored(state.fingerprint, self._fingerprint_inputs(source))
                if state is not None
                else (False, False)
            )
            if state is not None and unchanged and not force_reindex:
                # L3: an unchanged fingerprint is a healthy skip, not a failure.
                obs.warnings.append("fingerprint unchanged; skipping reindex")
                if is_legacy:
                    obs.warnings.append(
                        "fingerprint_v1_compat_skip; next content change emits fp_v2"
                    )
                await mark(IngestionStatus.SKIPPED)
                return obs.freeze()

            version_number = state.version_number + 1 if state else 1
            document_id = document_id_for(logical_id, version_number)

            await mark(IngestionStatus.PARSING)
            parsed = await parse_source(source, content)
            obs.durations["parse"] = stage_elapsed()
            if parsed.quality is not None:
                obs.warnings.extend(parsed.quality.parse_warnings)

            if parsed.status is ParseStatus.NEEDS_OCR:
                obs.failure_reason = (
                    parsed.failure_reason or "Document requires OCR before ingestion"
                )
                logger.warning(
                    "ingestion_needs_ocr",
                    extra={
                        "source_id": source.source_id,
                        "filename": source.filename,
                        "failure_reason": obs.failure_reason,
                    },
                )
                await mark(IngestionStatus.NEEDS_OCR)
                return obs.freeze()
            if parsed.status is not ParseStatus.PARSED or parsed.document is None:
                obs.failure_reason = parsed.failure_reason or f"parse {parsed.status.value}"
                await mark(IngestionStatus.FAILED)
                return obs.freeze()
            tree = parsed.document

            await mark(IngestionStatus.BUILDING_PARENTS)
            chunk_context = self._chunk_context(source, document_id)
            parents = SectionParentChunker(chunk_context, self._chunking_settings).build_parents(
                tree
            )
            obs.durations["parents"] = stage_elapsed()

            await mark(IngestionStatus.BUILDING_CHILDREN)
            child_chunker = SentenceChildChunker(chunk_context, self._chunking_settings)
            children = [
                (child, parent)
                for parent in parents
                for child in child_chunker.build_children(parent)
            ]
            obs.durations["children"] = stage_elapsed()

            await mark(IngestionStatus.EMBEDDING)
            try:
                heartbeat_fn = self._heartbeat
                batch_cb = (
                    (lambda *a, **k: heartbeat_fn(obs.job_id)) if heartbeat_fn is not None else None
                )
                vectors = await self._embedding.embed_documents(
                    [child.embedding_text for child, _ in children],
                    batch_callback=batch_cb,
                )
            except TypeError:
                vectors = await self._embedding.embed_documents(
                    [child.embedding_text for child, _ in children]
                )
            obs.durations["embedding"] = stage_elapsed()

            await mark(IngestionStatus.PERSISTING)
            async with self._transaction.transaction() as session:
                result = await self._repository.persist_candidate(
                    session,
                    source=source,
                    logical_document_id=logical_id,
                    document_id=document_id,
                    version_number=version_number,
                    fingerprint=fingerprint,
                    parser_name=obs.parser_name,
                    parser_version=obs.parser_version,
                    parents=parents,
                    children=[
                        (child, vector)
                        for (child, _), vector in zip(children, vectors, strict=True)
                    ],
                )
                persisted_id, persisted_version = result[0], result[1]
                is_dedup = getattr(
                    result,
                    "is_deduplicated",
                    persisted_id != document_id and persisted_version < version_number,
                )
                if is_dedup:
                    # Deduplicated: active version with same fingerprint already committed
                    obs.status = IngestionStatus.SKIPPED
                    obs.document_id = persisted_id
                    obs.document_version = persisted_version
                    return obs.freeze()

                await self._repository.activate_candidate(
                    session, logical_document_id=logical_id, document_id=persisted_id
                )
            obs.durations["persist"] = stage_elapsed()

            obs.document_id = persisted_id
            obs.document_version = persisted_version
            obs.counts["parent"] = len(parents)
            obs.counts["child"] = sum(1 for c, _ in children if c.level is ChunkLevel.CHILD)
            obs.counts["table_child"] = sum(
                1 for c, _ in children if c.level is ChunkLevel.TABLE_CHILD
            )
            await mark(IngestionStatus.COMPLETED)
            return obs.freeze()
        except Exception as exc:  # noqa: BLE001 - batch isolation contract
            obs.failure_reason = f"{type(exc).__name__}: {exc}"
            obs.status = IngestionStatus.FAILED
            try:
                await mark(IngestionStatus.FAILED)
            except Exception as mark_exc:  # noqa: BLE001 - never mask the original failure
                logger.warning("ingestion_mark_failed_error", error=str(mark_exc))
            return obs.freeze()

    async def sync_decision(
        self, source: SourceDocument
    ) -> tuple[str, StoredFingerprintState | None]:
        """NEW / MODIFIED / UNCHANGED without ingesting (spec P9D-4)."""
        inputs = self._fingerprint_inputs(source)
        logical_id = logical_document_id_for(source.source_id)
        state = await self._repository.latest_state(logical_id)
        if state is None:
            return "NEW", None
        unchanged, _is_legacy = fingerprint_matches_stored(state.fingerprint, inputs)
        if unchanged:
            return "UNCHANGED", state
        return "MODIFIED", state

    async def check_legacy_fingerprint(
        self, source: SourceDocument
    ) -> tuple[bool, StoredFingerprintState | None]:
        """Check if stored document has an active legacy fp_v1 fingerprint requiring upgrade."""
        inputs = self._fingerprint_inputs(source)
        logical_id = logical_document_id_for(source.source_id)
        state = await self._repository.latest_state(logical_id)
        if state is None:
            return False, None
        unchanged, is_legacy = fingerprint_matches_stored(state.fingerprint, inputs)
        return (unchanged and is_legacy), state

    async def deactivate_source(self, source_id: str) -> bool:
        """Deleted-source policy: archive versions, never hard-delete."""
        logical_id = logical_document_id_for(source_id)
        async with self._transaction.transaction() as session:
            return await self._repository.deactivate_logical_document(session, logical_id)

    def _fingerprint_inputs(self, source: SourceDocument) -> FingerprintInputs:
        embedding = get_settings().embedding
        chunking = self._chunking_settings
        ocr_meta = source.metadata.get("ocr_sidecar") if isinstance(source.metadata, dict) else None
        ocr_source_checksum = (
            ocr_meta.get("source_checksum") if isinstance(ocr_meta, dict) else None
        )
        ocr_engine = ocr_meta.get("engine") if isinstance(ocr_meta, dict) else None
        ocr_engine_version = ocr_meta.get("engine_version") if isinstance(ocr_meta, dict) else None
        ocr_device = ocr_meta.get("device") if isinstance(ocr_meta, dict) else None
        return FingerprintInputs(
            source_id=source.source_id,
            checksum=source.checksum,
            modified_at=source.modified_at,
            size_bytes=source.size_bytes,
            parser_version=_parser_version_for(source),
            parent_chunker_version=PARENT_CHUNKER_VERSION,
            child_chunker_version=CHILD_CHUNKER_VERSION,
            embedding_model=embedding.model,
            embedding_dimensions=embedding.dimensions,
            parent_target_tokens=chunking.parent_target_tokens,
            child_target_tokens=chunking.child_target_tokens,
            parent_hard_max_tokens=chunking.parent_hard_max_tokens,
            child_hard_max_tokens=chunking.child_hard_max_tokens,
            ocr_source_checksum=ocr_source_checksum,
            ocr_engine=ocr_engine,
            ocr_engine_version=ocr_engine_version,
            ocr_device=ocr_device,
        )

    def _chunk_context(self, source: SourceDocument, document_id: str) -> ChunkContext:
        embedding = get_settings().embedding
        return ChunkContext(
            document_id=document_id,
            document_version_id=document_id,
            source_id=source.source_id,
            title=source.filename,
            filename=source.filename,
            mime_type=source.mime_type,
            source_type=source.source_type,
            embedding_model=embedding.model,
        )


class _Obs:
    """Mutable accumulator finalized into the frozen observability record."""

    def __init__(self, job_id: str, source: SourceDocument, content_size: int) -> None:
        self.job_id = job_id
        self.source_id = source.source_id
        self.status = IngestionStatus.QUEUED
        self.source_size_bytes = content_size
        self.document_id: str | None = None
        self.document_version: int | None = None
        self.warnings: list[str] = []
        self.failure_reason: str | None = None
        self.parser_name = _parser_name_for(source)
        self.parser_version = _parser_version_for(source)
        self.durations: dict[str, float] = {
            "parse": 0.0,
            "parents": 0.0,
            "children": 0.0,
            "embedding": 0.0,
            "persist": 0.0,
        }
        self.counts = {"parent": 0, "child": 0, "table_child": 0}

    def freeze(self) -> IngestionObservability:
        embedding = get_settings().embedding
        return IngestionObservability(
            job_id=self.job_id,
            source_id=self.source_id,
            document_id=self.document_id,
            document_version=self.document_version,
            status=self.status,
            source_size_bytes=self.source_size_bytes,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            parent_chunker_version=PARENT_CHUNKER_VERSION,
            child_chunker_version=CHILD_CHUNKER_VERSION,
            embedding_model=embedding.model,
            parse_duration_seconds=self.durations["parse"],
            parent_build_duration_seconds=self.durations["parents"],
            child_build_duration_seconds=self.durations["children"],
            embedding_duration_seconds=self.durations["embedding"],
            persist_duration_seconds=self.durations["persist"],
            parent_count=self.counts["parent"],
            child_count=self.counts["child"],
            table_child_count=self.counts["table_child"],
            warnings=tuple(self.warnings),
            failure_reason=self.failure_reason,
        )
