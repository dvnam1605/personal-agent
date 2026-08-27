"""Integration tests: full ingestion pipeline on live PostgreSQL (spec P9D-6)."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.domain.models.documents import SourceDocument
from app.domain.models.ingestion import IngestionStatus
from app.infrastructure.db.models import Document, DocumentChunk, IngestionJobRecord
from app.services.ingestion.embedding import EmbeddingContract, LocalEmbeddingService
from app.services.ingestion.jobs import SqlAlchemyIngestionJobStore, run_batch
from app.services.ingestion.orchestrator import (
    IngestionOrchestrator,
    logical_document_id_for,
)

POSTGRES_TEST_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5434/assistant"
)
REPO_ROOT = Path(__file__).resolve().parents[2]
MD_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "parsing" / "markdown" / "preparsed_sample.md"
DOCX_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "parsing" / "docx" / "headings_paragraphs.docx"
PDF_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "parsing" / "pdf" / "normal_text.pdf"


class Deterministic1024Backend:
    """Cheap offline stand-in producing valid 1024-dim vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[(len(t) % 13) / 13.0] * 1024 for t in texts]


@pytest_asyncio.fixture
async def pg_session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    admin_engine = create_async_engine(POSTGRES_TEST_URL, echo=False)
    try:
        async with admin_engine.connect():
            pass
    except Exception as exc:
        await admin_engine.dispose()
        pytest.skip(f"PostgreSQL integration instance not available at {POSTGRES_TEST_URL}: {exc}")

    schema_name = f"p9d_test_{uuid.uuid4().hex}"
    session_engine = create_async_engine(
        POSTGRES_TEST_URL,
        echo=False,
        connect_args={"server_settings": {"search_path": f"{schema_name},public"}},
    )
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
        alembic_config.set_main_option("sqlalchemy.url", POSTGRES_TEST_URL)
        alembic_config.set_main_option("version_table_schema", schema_name)
        alembic_config.attributes["connect_args"] = {
            "server_settings": {"search_path": f"{schema_name},public"}
        }
        # Keep fileConfig out of the pytest process (would disable existing
        # loggers for every subsequently-run test in the session).
        alembic_config.attributes["configure_logger"] = False
        await asyncio.to_thread(command.upgrade, alembic_config, "head")

        yield async_sessionmaker(session_engine, expire_on_commit=False)
    finally:
        await session_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await admin_engine.dispose()


def make_orchestrator(session_maker) -> IngestionOrchestrator:
    from app.infrastructure.db.ingestion_repository import (
        SqlAlchemyIngestionRepository,
        SqlAlchemyUnitOfWork,
    )

    embedding = LocalEmbeddingService(
        Deterministic1024Backend(),
        contract=EmbeddingContract("AITeamVN/Vietnamese_Embedding", 1024),
    )
    return IngestionOrchestrator(
        repository=SqlAlchemyIngestionRepository(session_maker),
        transaction=SqlAlchemyUnitOfWork(session_maker),
        embedding=embedding,
    )


def md_source(source_id: str = "md-it-1") -> SourceDocument:
    return SourceDocument(
        source_id=source_id, source_type="preparsed_markdown", filename="preparsed_sample.md"
    )


async def test_markdown_end_to_end_hierarchy_persisted(pg_session_maker) -> None:
    orchestrator = make_orchestrator(pg_session_maker)
    obs = await orchestrator.ingest_source(md_source(), MD_FIXTURE.read_bytes())

    assert obs.status is IngestionStatus.COMPLETED
    assert obs.parent_count >= 3 and obs.child_count >= 1 and obs.table_child_count >= 1

    async with pg_session_maker() as session:
        document = await session.get(Document, obs.document_id)
        assert document is not None
        assert document.is_active is True and document.status == "active"
        assert len(document.fingerprint) == 64
        chunks = (
            (
                await session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == obs.document_id)
                    .order_by(DocumentChunk.hierarchy_level, DocumentChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )
        parents = [c for c in chunks if c.node_type == "PARENT"]
        children = [c for c in chunks if c.node_type != "PARENT"]
        assert len(parents) == obs.parent_count
        assert len(children) == obs.child_count + obs.table_child_count
        assert all(child.parent_id in {p.id for p in parents} for child in children)
        assert all(c.embedding is not None and len(c.embedding) == 1024 for c in children)


async def test_docx_end_to_end_via_real_parser(pg_session_maker) -> None:
    orchestrator = make_orchestrator(pg_session_maker)
    source = SourceDocument(
        source_id="docx-it-1",
        source_type="local_fixture",
        filename="headings_paragraphs.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    obs = await orchestrator.ingest_source(source, DOCX_FIXTURE.read_bytes())
    assert obs.status is IngestionStatus.COMPLETED
    assert obs.parser_name == "docling"
    assert obs.child_count >= 1


async def test_pdf_end_to_end_via_real_docling(pg_session_maker) -> None:
    """Spec P9D-6 bullet 1: PDF parse -> chunks -> persisted hierarchy (review M2).

    Requires the docling model cache; skips where it cannot download/load.
    """
    orchestrator = make_orchestrator(pg_session_maker)
    source = SourceDocument(
        source_id="pdf-it-1",
        source_type="local_fixture",
        filename="normal_text.pdf",
        mime_type="application/pdf",
    )
    obs = await orchestrator.ingest_source(source, PDF_FIXTURE.read_bytes())
    if obs.status is IngestionStatus.FAILED and "docling" in (obs.failure_reason or ""):
        pytest.skip(f"docling engine unavailable in this environment: {obs.failure_reason}")

    assert obs.status is IngestionStatus.COMPLETED
    assert obs.parser_name == "docling"
    assert obs.parent_count >= 1 and obs.child_count >= 1

    async with pg_session_maker() as session:
        parents = (
            (
                await session.execute(
                    select(DocumentChunk).where(
                        DocumentChunk.document_id == obs.document_id,
                        DocumentChunk.node_type == "PARENT",
                    )
                )
            )
            .scalars()
            .all()
        )
        children = (
            (
                await session.execute(
                    select(DocumentChunk).where(
                        DocumentChunk.document_id == obs.document_id,
                        DocumentChunk.node_type != "PARENT",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(parents) == obs.parent_count
        assert len(children) == obs.child_count
        assert all(child.parent_id is not None for child in children)
        assert all(child.embedding is not None for child in children)


async def test_scanned_pdf_yields_needs_ocr_without_persisting(pg_session_maker) -> None:
    scanned = REPO_ROOT / "tests" / "fixtures" / "parsing" / "pdf" / "scanned.pdf"
    orchestrator = make_orchestrator(pg_session_maker)
    source = SourceDocument(
        source_id="scan-it-1",
        source_type="local_fixture",
        filename="scanned.pdf",
        mime_type="application/pdf",
    )
    obs = await orchestrator.ingest_source(source, scanned.read_bytes())
    if obs.status is IngestionStatus.FAILED and "docling" in (obs.failure_reason or ""):
        pytest.skip(f"docling engine unavailable in this environment: {obs.failure_reason}")

    assert obs.status is IngestionStatus.NEEDS_OCR
    async with pg_session_maker() as session:
        rows = (
            (await session.execute(select(Document).where(Document.external_id == "scan-it-1")))
            .scalars()
            .all()
        )
        assert rows == []


async def test_modified_creates_version_two_and_archives_one(pg_session_maker) -> None:
    orchestrator = make_orchestrator(pg_session_maker)
    original = MD_FIXTURE.read_bytes()
    v1 = await orchestrator.ingest_source(md_source(), original)

    modified = original + b"\n\n## Appended\n\nAdditional closing paragraph content."
    modified_source = SourceDocument(
        source_id="md-it-1",
        source_type="preparsed_markdown",
        filename="preparsed_sample.md",
        checksum="ef" * 32,
    )
    v2 = await orchestrator.ingest_source(modified_source, modified)

    assert (v1.document_version, v2.document_version) == (1, 2)
    async with pg_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(Document).where(
                        Document.logical_document_id == logical_document_id_for("md-it-1")
                    )
                )
            )
            .scalars()
            .all()
        )
        active = [row for row in rows if row.is_active]
        archived = [row for row in rows if not row.is_active]
        assert len(active) == 1 and active[0].version_number == 2
        assert any(row.version_number == 1 for row in archived)


async def test_failed_candidate_keeps_previous_active_searchable(pg_session_maker) -> None:
    orchestrator = make_orchestrator(pg_session_maker)
    v1 = await orchestrator.ingest_source(md_source(), MD_FIXTURE.read_bytes())
    bad = SourceDocument(source_id="md-it-1", source_type="upload", filename="broken.bin")
    failed = await orchestrator.ingest_source(bad, b"\xff\xff\xffnot-supported")

    assert failed.status is IngestionStatus.FAILED
    async with pg_session_maker() as session:
        previous = await session.get(Document, v1.document_id)
        assert previous is not None
        assert previous.is_active is True and previous.status == "active"


async def test_dimension_mismatch_rejected_loudly_rolls_back(pg_session_maker) -> None:
    from app.infrastructure.db.ingestion_repository import (
        SqlAlchemyIngestionRepository,
        SqlAlchemyUnitOfWork,
    )

    class WrongDims:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.5] * 768 for _ in texts]

    session_maker = pg_session_maker
    orchestrator = IngestionOrchestrator(
        repository=SqlAlchemyIngestionRepository(session_maker),
        transaction=SqlAlchemyUnitOfWork(session_maker),
        embedding=LocalEmbeddingService(
            WrongDims(), contract=EmbeddingContract("wrong-model", 1024), max_attempts=1
        ),
    )
    # Seed a good version first so rollback protection has something to protect.
    healthy = make_orchestrator(session_maker)
    v1 = await healthy.ingest_source(md_source(), MD_FIXTURE.read_bytes())
    assert v1.status is IngestionStatus.COMPLETED

    modified = SourceDocument(
        source_id="md-it-1",
        source_type="preparsed_markdown",
        filename="preparsed_sample.md",
        checksum="aa" * 32,
    )
    result = await orchestrator.ingest_source(modified, MD_FIXTURE.read_bytes() + b"\n\nmore")

    assert result.status is IngestionStatus.FAILED
    assert "dimension mismatch" in (result.failure_reason or "")
    async with session_maker() as session:
        previous = await session.get(Document, v1.document_id)
        assert previous is not None and previous.is_active is True
        orphans = (
            (
                await session.execute(
                    select(DocumentChunk).where(
                        DocumentChunk.document_id.in_(
                            select(Document.id).where(
                                Document.logical_document_id == logical_document_id_for("md-it-1"),
                                Document.version_number == 2,
                            )
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        assert orphans == []  # candidate fully rolled back


async def test_job_rows_recorded_by_pg_store(pg_session_maker) -> None:
    orchestrator = make_orchestrator(pg_session_maker)
    store = SqlAlchemyIngestionJobStore(pg_session_maker)
    results = await run_batch(
        orchestrator,
        [(md_source("md-jobs-1"), MD_FIXTURE.read_bytes())],
        store=store,
        worker_id="it-worker",
    )
    assert results[0].status is IngestionStatus.COMPLETED

    async with pg_session_maker() as session:
        job = await session.get(IngestionJobRecord, results[0].job_id)
        assert job is not None
        assert job.status == "COMPLETED"
        assert job.parent_count >= 1
