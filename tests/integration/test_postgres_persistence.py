"""Integration tests for PostgreSQL with pgvector and JSONB support."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.infrastructure.db.models import Memory, User

POSTGRES_TEST_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5434/assistant"
)


@pytest_asyncio.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    """Migrate a disposable schema on a live PostgreSQL instance if available."""
    admin_engine = create_async_engine(POSTGRES_TEST_URL, echo=False)
    try:
        async with admin_engine.connect():
            pass
    except Exception as exc:
        await admin_engine.dispose()
        pytest.skip(f"PostgreSQL integration instance not available at {POSTGRES_TEST_URL}: {exc}")

    schema_name = f"p3_test_{uuid4().hex}"
    session_engine = create_async_engine(
        POSTGRES_TEST_URL,
        echo=False,
        connect_args={"server_settings": {"search_path": f"{schema_name},public"}},
    )
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

        alembic_config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        alembic_config.set_main_option("sqlalchemy.url", POSTGRES_TEST_URL)
        alembic_config.set_main_option("version_table_schema", schema_name)
        alembic_config.attributes["connect_args"] = {
            "server_settings": {"search_path": f"{schema_name},public"}
        }
        # Keep fileConfig out of the pytest process (would disable existing
        # loggers for every subsequently-run test in the session).
        alembic_config.attributes["configure_logger"] = False
        await asyncio.to_thread(command.upgrade, alembic_config, "head")

        session_maker = async_sessionmaker(session_engine, expire_on_commit=False)
        async with session_maker() as session:
            yield session
    finally:
        await session_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_postgres_migration_contract(pg_session: AsyncSession) -> None:
    """Run the real Alembic path and validate P3 contracts plus P5 Google storage."""
    assert await pg_session.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")) == 1
    assert await pg_session.scalar(text("SELECT version_num FROM alembic_version")) == "0006"
    assert (
        await pg_session.scalar(text("SELECT to_regclass('google_integrations')"))
        == "google_integrations"
    )

    columns = set(
        (
            await pg_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'assistant_runs'"
                )
            )
        ).scalars()
    )
    assert {"state_version", "status", "completed_at"}.issubset(columns)

    indexes = set(
        (
            await pg_session.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'audit_outbox'")
            )
        ).scalars()
    )
    assert {"ix_audit_outbox_pending", "ix_audit_outbox_run"}.issubset(indexes)

    constraints = set(
        (
            await pg_session.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = 'document_chunks'::regclass"
                )
            )
        ).scalars()
    )
    assert "fk_document_chunks_parent_id" in constraints

    document_constraints = set(
        (
            await pg_session.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'documents'::regclass")
            )
        ).scalars()
    )
    assert {"ck_documents_status", "ck_documents_version_positive"}.issubset(document_constraints)
    approval_constraints = set(
        (
            await pg_session.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'approval_requests'::regclass"
                )
            )
        ).scalars()
    )
    assert {
        "ck_approval_requests_status",
        "ck_approval_requests_decision_consistency",
    }.issubset(approval_constraints)


@pytest.mark.asyncio
async def test_pgvector_cosine_distance_query(pg_session: AsyncSession) -> None:
    """Verify pgvector embedding insertion and cosine distance query."""
    user = User(email="pg_vec_user@example.com", full_name="Vector User")
    pg_session.add(user)
    await pg_session.flush()

    # Create two memories with 1024-dim dummy vectors
    vec1 = [0.1] * 1024
    vec2 = [0.9] * 1024

    mem1 = Memory(
        user_id=user.id,
        memory_type="user_fact",
        content="Likes fast response",
        embedding=vec1,
        embedding_model="AITeamVN/Vietnamese_Embedding",
        embedding_dimensions=1024,
    )
    mem2 = Memory(
        user_id=user.id,
        memory_type="user_fact",
        content="Prefers detailed analysis",
        embedding=vec2,
        embedding_model="AITeamVN/Vietnamese_Embedding",
        embedding_dimensions=1024,
    )
    pg_session.add_all([mem1, mem2])
    await pg_session.flush()

    assert mem1.id is not None
    assert mem2.id is not None

    nearest = await pg_session.scalar(
        select(Memory)
        .where(Memory.user_id == user.id)
        .order_by(Memory.embedding.cosine_distance(vec1))
        .limit(1)
    )
    assert nearest is not None
    assert nearest.id == mem1.id
