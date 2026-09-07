"""Unit tests for SqlAlchemyIngestionRepository concurrency and deduplication."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.models.chunks import ChildChunkDraft, ParentChunkDraft
from app.domain.models.documents import SourceDocument
from app.infrastructure.db.base import Base
from app.infrastructure.db.ingestion_repository import SqlAlchemyIngestionRepository
from app.infrastructure.db.models import DocumentChunk, User


@pytest.fixture
async def session_maker():
    """Create an in-memory SQLite async engine and session factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        # Create a test user
        user = User(id="usr_test", email="test@example.com", full_name="Test User", is_active=True)
        session.add(user)
        await session.commit()

    yield maker
    await engine.dispose()


def _build_test_source(source_id: str = "src-1") -> SourceDocument:
    return SourceDocument(
        source_id=source_id,
        source_type="preparsed_markdown",
        filename="sample.md",
        checksum="c" * 64,
        size_bytes=32,
        metadata={"user_id": "usr_test", "title": "Sample"},
    )


def _build_drafts(doc_id: str) -> tuple[ParentChunkDraft, ChildChunkDraft]:
    parent = ParentChunkDraft(
        id=f"{doc_id}-p0000000",
        document_id=doc_id,
        document_version_id=doc_id,
        source_id="src-1",
        title="Sample",
        raw_text="# Sample\nHello world",
        token_count=10,
        content_hash="a" * 64,
        ordinal=0,
        parent_chunker_version="1.0",
    )
    child = ChildChunkDraft(
        id=f"{doc_id}-c0000000",
        document_id=doc_id,
        document_version_id=doc_id,
        source_id="src-1",
        title="Sample",
        raw_text="Hello world",
        token_count=5,
        content_hash="b" * 64,
        parent_id=f"{doc_id}-p0000000",
        chunk_index=0,
        embedding_text="Hello world",
        child_chunker_version="1.0",
        filename="sample.md",
    )
    return parent, child


@pytest.mark.asyncio
async def test_persist_candidate_dedup(session_maker: async_sessionmaker[AsyncSession]) -> None:
    """Test that when fingerprint matches active row, existing id and version are returned."""
    repo = SqlAlchemyIngestionRepository(session_maker)
    source = _build_test_source()
    logical_id = "doc-logical-1"
    fingerprint = "f" * 64

    parent, child = _build_drafts("doc-v1")

    # 1. First persist
    async with session_maker() as session:
        doc_id_1, ver_1 = await repo.persist_candidate(
            session,
            source=source,
            logical_document_id=logical_id,
            document_id="doc-v1",
            version_number=1,
            fingerprint=fingerprint,
            parser_name="markdown",
            parser_version="1.0",
            parents=[parent],
            children=[(child, [0.1] * 1024)],
        )
        await session.commit()

    assert ver_1 == 1

    # Activate candidate
    async with session_maker() as session:
        await repo.activate_candidate(session, logical_document_id=logical_id, document_id=doc_id_1)
        await session.commit()

    # 2. Persist again with identical fingerprint
    async with session_maker() as session:
        doc_id_2, ver_2 = await repo.persist_candidate(
            session,
            source=source,
            logical_document_id=logical_id,
            document_id="doc-v2",
            version_number=2,
            fingerprint=fingerprint,
            parser_name="markdown",
            parser_version="1.0",
            parents=[parent],
            children=[(child, [0.1] * 1024)],
        )
        await session.commit()

    # Should dedup to existing active version without creating new rows
    assert doc_id_2 == doc_id_1
    assert ver_2 == 1


@pytest.mark.asyncio
async def test_persist_candidate_concurrent_version_bump(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Test that if a version already exists, version number is bumped to prevent PK collision."""
    repo = SqlAlchemyIngestionRepository(session_maker)
    source = _build_test_source()
    logical_id = "doc-logical-race"

    parent, child = _build_drafts("doc-initial")

    # Pre-existing version 1
    async with session_maker() as session:
        doc_id_1, ver_1 = await repo.persist_candidate(
            session,
            source=source,
            logical_document_id=logical_id,
            document_id="doc-initial",
            version_number=1,
            fingerprint="1" * 64,
            parser_name="markdown",
            parser_version="1.0",
            parents=[parent],
            children=[(child, [0.1] * 1024)],
        )
        await session.commit()

    async with session_maker() as session:
        await repo.activate_candidate(session, logical_document_id=logical_id, document_id=doc_id_1)
        await session.commit()

    # Caller raced and thought it was version 1 too, but with new fingerprint
    parent2, child2 = _build_drafts("doc-raced")
    async with session_maker() as session:
        doc_id_2, ver_2 = await repo.persist_candidate(
            session,
            source=source,
            logical_document_id=logical_id,
            document_id="doc-raced",
            version_number=1,
            fingerprint="2" * 64,
            parser_name="markdown",
            parser_version="1.0",
            parents=[parent2],
            children=[(child2, [0.1] * 1024)],
        )
        await session.commit()

    # System should have recalculated actual_version = 2
    assert ver_2 == 2
    assert doc_id_2 != "doc-raced"
    assert doc_id_2.startswith("doc-")


@pytest.mark.asyncio
async def test_persist_candidate_concurrent_chunk_id_collision_prevention(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Test that two workers sharing identical initial chunk IDs do not collide on DocumentChunk.id PK."""
    repo = SqlAlchemyIngestionRepository(session_maker)
    source = _build_test_source()
    logical_id = "doc-logical-chunk-collision"

    # Both workers built drafts using the same base doc_id "doc-v1"
    parent1, child1 = _build_drafts("doc-v1")
    parent2, child2 = _build_drafts("doc-v1")
    assert parent1.id == parent2.id
    assert child1.id == child2.id

    # Worker 1 commits version 1
    async with session_maker() as session:
        doc_id_1, ver_1 = await repo.persist_candidate(
            session,
            source=source,
            logical_document_id=logical_id,
            document_id="doc-v1",
            version_number=1,
            fingerprint="1" * 64,
            parser_name="markdown",
            parser_version="1.0",
            parents=[parent1],
            children=[(child1, [0.1] * 1024)],
        )
        await session.commit()

    async with session_maker() as session:
        await repo.activate_candidate(session, logical_document_id=logical_id, document_id=doc_id_1)
        await session.commit()

    # Worker 2 had raced, has a newer fingerprint but still holds chunk IDs derived from "doc-v1"
    async with session_maker() as session:
        doc_id_2, ver_2 = await repo.persist_candidate(
            session,
            source=source,
            logical_document_id=logical_id,
            document_id="doc-v1",
            version_number=1,
            fingerprint="2" * 64,
            parser_name="markdown",
            parser_version="1.0",
            parents=[parent2],
            children=[(child2, [0.2] * 1024)],
        )
        await session.commit()

    assert ver_2 == 2
    assert doc_id_2 != "doc-v1"

    # Verify both version's chunks exist and have different IDs in the database
    async with session_maker() as session:
        chunks_v1 = (
            (await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc_id_1)))
            .scalars()
            .all()
        )
        chunks_v2 = (
            (await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc_id_2)))
            .scalars()
            .all()
        )
        assert len(chunks_v1) == 2
        assert len(chunks_v2) == 2
        ids_v1 = {c.id for c in chunks_v1}
        ids_v2 = {c.id for c in chunks_v2}
        assert ids_v1.isdisjoint(ids_v2), f"Chunk IDs collided: {ids_v1 & ids_v2}"



@pytest.mark.asyncio
async def test_postgres_dialect_detection_triggers_for_update() -> None:
    """Verify that postgres dialect triggers with_for_update on query."""
    mock_session = MagicMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.get_bind.return_value = mock_bind

    # Mock execute result
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    # Running repo with mock session
    repo = SqlAlchemyIngestionRepository(MagicMock())
    source = _build_test_source()

    try:
        await repo.persist_candidate(
            mock_session,
            source=source,
            logical_document_id="doc-test-pg",
            document_id="doc-1",
            version_number=1,
            fingerprint="3" * 64,
            parser_name="markdown",
            parser_version="1.0",
            parents=[],
            children=[],
        )
    except Exception:
        # We only care that execute was called with a query that has for_update
        pass

    assert mock_session.execute.called
    query = mock_session.execute.call_args[0][0]
    assert query._for_update_arg is not None
