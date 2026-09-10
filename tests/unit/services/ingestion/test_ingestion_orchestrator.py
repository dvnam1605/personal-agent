"""Hermetic unit tests for the ingestion orchestrator (spec P9D-2/4/5)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.domain.models.ingestion import IngestionStatus, StoredFingerprintState
from app.domain.models.ingestion.chunks import ParentChunkDraft
from app.domain.models.ingestion.documents import SourceDocument
from app.services.ingestion.embedding import EmbeddingContract, LocalEmbeddingService
from app.services.ingestion.orchestrator import (
    IngestionOrchestrator,
    logical_document_id_for,
)
from app.services.ingestion.parsing.docling_parser import DoclingDocumentParser
from tests.unit.services._repo import repo_root

REPO_ROOT = repo_root()
MD_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "parsing" / "markdown" / "preparsed_sample.md"


class FakeBackend:
    def __init__(self, dimensions: int = 8) -> None:
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7)] * self.dimensions for t in texts]


@dataclass
class StoredDocument:
    document_id: str
    logical_document_id: str
    version_number: int
    fingerprint: str
    is_active: bool
    status: str
    chunks: list[dict[str, Any]] = field(default_factory=list)


class InMemoryRepository:
    def __init__(self) -> None:
        self.documents: dict[str, StoredDocument] = {}

    async def latest_state(self, logical_document_id: str) -> StoredFingerprintState | None:
        rows = [
            doc for doc in self.documents.values() if doc.logical_document_id == logical_document_id
        ]
        if not rows:
            return None
        latest = max(rows, key=lambda d: d.version_number)
        return StoredFingerprintState(
            logical_document_id=latest.logical_document_id,
            document_id=latest.document_id,
            version_number=latest.version_number,
            fingerprint=latest.fingerprint,
            is_active=latest.is_active,
        )

    async def persist_candidate(self, session: object, **kwargs: Any) -> tuple[str, int]:
        stored = StoredDocument(
            document_id=kwargs["document_id"],
            logical_document_id=kwargs["logical_document_id"],
            version_number=kwargs["version_number"],
            fingerprint=kwargs["fingerprint"],
            is_active=False,
            status="processing",
        )
        for parent in kwargs["parents"]:
            assert isinstance(parent, ParentChunkDraft)
            stored.chunks.append({"id": parent.id, "level": "PARENT", "parent": None})
        for child, vector in kwargs["children"]:
            stored.chunks.append(
                {
                    "id": child.id,
                    "level": child.level.value,
                    "parent": child.parent_id,
                    "vector": vector,
                }
            )
        self.documents[stored.document_id] = stored
        return stored.document_id, stored.version_number

    async def activate_candidate(
        self, session: object, *, logical_document_id: str, document_id: str
    ) -> None:
        for doc in self.documents.values():
            if doc.logical_document_id == logical_document_id:
                doc.is_active = False
                if doc.status == "active":
                    doc.status = "archived"
        candidate = self.documents[document_id]
        candidate.is_active = True
        candidate.status = "active"

    async def deactivate_logical_document(self, session: object, logical_document_id: str) -> bool:
        changed = False
        for doc in self.documents.values():
            if doc.logical_document_id == logical_document_id and doc.status != "archived":
                doc.status = "archived"
                doc.is_active = False
                changed = True
        return changed


class NullTx:
    @contextlib.asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield object()


def make_source(**overrides: Any) -> SourceDocument:
    values: dict[str, Any] = {
        "source_id": "md-src-1",
        "source_type": "preparsed_markdown",
        "filename": "preparsed_sample.md",
    }
    values.update(overrides)
    return SourceDocument(**values)


def make_orchestrator() -> tuple[IngestionOrchestrator, InMemoryRepository]:
    repository = InMemoryRepository()
    embedding = LocalEmbeddingService(FakeBackend(), contract=EmbeddingContract("fake", 8))
    return IngestionOrchestrator(
        repository=repository, transaction=NullTx(), embedding=embedding
    ), repository


async def test_new_source_completes_full_pipeline() -> None:
    orchestrator, repository = make_orchestrator()
    obs = await orchestrator.ingest_source(make_source(), MD_FIXTURE.read_bytes())

    assert obs.status is IngestionStatus.COMPLETED
    assert obs.document_id is not None and obs.document_version == 1
    assert obs.parent_count >= 1 and obs.child_count + obs.table_child_count >= 1
    assert all(
        d >= 0
        for d in (
            obs.parse_duration_seconds,
            obs.parent_build_duration_seconds,
            obs.child_build_duration_seconds,
            obs.embedding_duration_seconds,
            obs.persist_duration_seconds,
        )
    )
    assert obs.document_id is not None
    stored = repository.documents[obs.document_id]
    levels = [chunk["level"] for chunk in stored.chunks]
    assert "PARENT" in levels
    active_chunks = [
        c for c in stored.chunks if c.get("vector") is not None or c["level"] == "PARENT"
    ]
    assert active_chunks


async def test_unchanged_fingerprint_skips() -> None:
    orchestrator, repository = make_orchestrator()
    content = MD_FIXTURE.read_bytes()
    first = await orchestrator.ingest_source(make_source(), content)
    second = await orchestrator.ingest_source(make_source(), content)
    assert first.status is IngestionStatus.COMPLETED
    assert second.status is IngestionStatus.SKIPPED
    assert second.failure_reason is None
    assert any("unchanged" in w for w in second.warnings)
    assert len(repository.documents) == 1


async def test_modified_content_creates_new_version_and_archives_old() -> None:
    orchestrator, repository = make_orchestrator()
    original = MD_FIXTURE.read_bytes()
    modified = original + b"\n\n## Appended\n\nExtra closing paragraph with more text."
    v1 = await orchestrator.ingest_source(make_source(), original)
    # A real sync flow sees a changed checksum/mtime on the source metadata.
    modified_source = make_source(checksum="ef" * 32, size_bytes=len(modified))
    v2 = await orchestrator.ingest_source(modified_source, modified)

    assert (v1.document_version, v2.document_version) == (1, 2)
    assert v1.document_id is not None and v2.document_id is not None
    old = repository.documents[v1.document_id]
    assert old.status == "archived" and old.is_active is False
    new = repository.documents[v2.document_id]
    assert new.status == "active" and new.is_active is True


async def test_unsupported_input_fails_without_persisting() -> None:
    orchestrator, repository = make_orchestrator()
    bad = SourceDocument(source_id="bin-1", source_type="upload", filename="blob.bin")
    obs = await orchestrator.ingest_source(bad, b"\x00\x01\x02\xffnot-a-document")
    assert obs.status is IngestionStatus.FAILED
    assert "unsupported" in (obs.failure_reason or "").lower()
    assert repository.documents == {}


async def test_needs_ocr_typed_status_queues_without_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.models.ingestion.parsed_document import ParsedDocument, ParsedDocumentMetadata

    async def empty_parse(self: DoclingDocumentParser, source: SourceDocument, content: bytes):
        return ParsedDocument(
            metadata=ParsedDocumentMetadata(
                source_id=source.source_id,
                filename=source.filename,
                parser_name="docling",
                parser_version="test",
            ),
        )

    monkeypatch.setattr(DoclingDocumentParser, "parse", empty_parse)
    orchestrator, repository = make_orchestrator()
    scan = SourceDocument(source_id="scan-1", source_type="upload", filename="scanned.pdf")
    obs = await orchestrator.ingest_source(scan, b"%PDF-scan")
    assert obs.status is IngestionStatus.NEEDS_OCR
    assert repository.documents == {}


async def test_failed_candidate_keeps_previous_active_version() -> None:
    orchestrator, repository = make_orchestrator()
    good = MD_FIXTURE.read_bytes()
    v1 = await orchestrator.ingest_source(make_source(), good)
    broken = SourceDocument(source_id="md-src-1", source_type="upload", filename="broken.bin")
    failed = await orchestrator.ingest_source(broken, b"\xff\xff\xff")

    assert failed.status is IngestionStatus.FAILED
    assert v1.document_id is not None
    previous = repository.documents[v1.document_id]
    assert previous.status == "active" and previous.is_active is True


async def test_sync_decision_lifecycle() -> None:
    orchestrator, _ = make_orchestrator()
    source = make_source()
    decision, state = await orchestrator.sync_decision(source)
    assert decision == "NEW" and state is None

    await orchestrator.ingest_source(source, MD_FIXTURE.read_bytes())
    decision, state = await orchestrator.sync_decision(source)
    assert decision == "UNCHANGED" and state is not None

    modified = SourceDocument(
        source_id="md-src-1",
        source_type="preparsed_markdown",
        filename="preparsed_sample.md",
        checksum="cd" * 32,
    )
    decision, state = await orchestrator.sync_decision(modified)
    assert decision == "MODIFIED" and state is not None


async def test_deactivate_archives_versions() -> None:
    orchestrator, repository = make_orchestrator()
    obs = await orchestrator.ingest_source(make_source(), MD_FIXTURE.read_bytes())
    assert obs.document_id is not None
    changed = await orchestrator.deactivate_source("md-src-1")
    assert changed is True
    stored = repository.documents[obs.document_id]
    assert stored.status == "archived" and stored.is_active is False


async def test_logical_document_id_is_deterministic_and_bounded() -> None:
    first = logical_document_id_for("drive-file-123")
    second = logical_document_id_for("drive-file-123")
    assert first == second
    assert first.startswith("ldg-") and len(first) == 36


async def test_orchestrator_respects_custom_chunking_settings() -> None:
    from app.core.config import ChunkingSettings

    payload = MD_FIXTURE.read_bytes()
    default_orch, default_repo = make_orchestrator()
    default_obs = await default_orch.ingest_source(make_source(), payload)
    assert default_obs.status is IngestionStatus.COMPLETED
    default_fp = next(iter(default_repo.documents.values())).fingerprint

    repository = InMemoryRepository()
    embedding = LocalEmbeddingService(FakeBackend(), contract=EmbeddingContract("fake", 8))
    custom_settings = ChunkingSettings(
        child_target_tokens=10,
        child_hard_max_tokens=20,
        merge_small_nodes_below_tokens=10,
    )
    orchestrator = IngestionOrchestrator(
        repository=repository,
        transaction=NullTx(),
        embedding=embedding,
        chunking_settings=custom_settings,
    )
    obs = await orchestrator.ingest_source(make_source(), payload)
    assert obs.status is IngestionStatus.COMPLETED
    assert obs.child_count > 0
    custom_fp = next(iter(repository.documents.values())).fingerprint
    assert custom_fp != default_fp
