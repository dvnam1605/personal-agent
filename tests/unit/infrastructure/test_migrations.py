"""Unit tests for database migration and schema completeness."""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

import app.infrastructure.db.models  # noqa: F401
from alembic import command
from app.core.config import settings
from app.infrastructure.db.base import Base


def test_alembic_upgrade_head_creates_hardened_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the actual Alembic upgrade path through the P5 schema revision."""
    database_path = tmp_path / "p5_migration.db"
    monkeypatch.setattr(settings.database, "url", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    repository_root = Path(__file__).resolve().parents[3]
    alembic_config = Config(str(repository_root / "alembic.ini"))
    # Keep fileConfig out of the pytest process: its disable_existing_loggers
    # default would mute every imported logger for all subsequently-run tests.
    alembic_config.attributes["configure_logger"] = False

    command.upgrade(alembic_config, "head")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(assistant_runs)").fetchall()
        }
        outbox_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(audit_outbox)").fetchall()
        }
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert "audit_outbox" in tables
    assert "google_integrations" in tables
    assert "ingestion_jobs" in tables
    document_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(documents)").fetchall()
    }
    job_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(ingestion_jobs)").fetchall()
    }
    assert {"state_version", "telemetry_degraded"}.issubset(run_columns)
    assert {"claimed_by", "claimed_at", "next_attempt_at"}.issubset(outbox_columns)
    assert "fingerprint" in document_columns
    assert {
        "status",
        "payload",
        "timings",
        "parent_count",
        "child_count",
        "table_child_count",
        "failure_reason",
        "claimed_by",
        "claimed_at",
    }.issubset(job_columns)
    assert version == "0008"


@pytest.mark.asyncio
async def test_all_p5_tables_created() -> None:
    """Verify all mapped tables, including durable audit and Google integration storage."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def inspect_tables(sync_conn):
            inspector = inspect(sync_conn)
            return set(inspector.get_table_names())

        table_names = await conn.run_sync(inspect_tables)

    await engine.dispose()

    expected_tables = {
        "users",
        "assistant_runs",
        "conversations",
        "messages",
        "entities",
        "memories",
        "documents",
        "document_chunks",
        "tool_executions",
        "llm_executions",
        "approval_requests",
        "audit_events",
        "audit_outbox",
        "skills",
        "workflow_runs",
        "google_integrations",
    }

    assert expected_tables.issubset(table_names), f"Missing tables: {expected_tables - table_names}"
