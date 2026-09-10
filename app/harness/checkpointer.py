"""Checkpointer derivation and lifecycle on PostgreSQL/psycopg3 (spec P18-03, §18A.4, §18A.6).

LangGraph execution checkpointer on PostgreSQL with psycopg3 DSN derived from DATABASE__URL.
Resumability only; audit and run state remain in first-party persistence (§18A.4).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from app.core.config import Environment, settings

logger = logging.getLogger(__name__)

# Shared memory saver cache for in-memory and test executions
_TEST_MEMORY_SAVER: MemorySaver | None = None


def get_checkpointer_dsn(url: str | None = None) -> str:
    """Derive psycopg3 checkpointer DSN from SQLAlchemy DATABASE__URL (spec §18A.6).

    Strips the `+asyncpg` dialect modifier so psycopg[binary] can establish direct
    checkpointer connections without affecting SQLAlchemy asyncpg sessions.
    """
    raw_url = url or settings.database.url
    return raw_url.replace("postgresql+asyncpg://", "postgresql://")


def get_memory_checkpointer() -> MemorySaver:
    """Return a shared in-memory checkpointer for testing and local dev."""
    global _TEST_MEMORY_SAVER
    if _TEST_MEMORY_SAVER is None:
        _TEST_MEMORY_SAVER = MemorySaver()
    return _TEST_MEMORY_SAVER


def reset_memory_checkpointer() -> MemorySaver:
    """Reset the test checkpointer state (for isolated unit tests)."""
    global _TEST_MEMORY_SAVER
    _TEST_MEMORY_SAVER = MemorySaver()
    return _TEST_MEMORY_SAVER


_CONFIGURED_CHECKPOINTER: Any | None = None


def set_default_checkpointer(checkpointer: Any | None) -> None:
    """Register the globally configured checkpointer (e.g. AsyncPostgresSaver from lifespan)."""
    global _CONFIGURED_CHECKPOINTER
    _CONFIGURED_CHECKPOINTER = checkpointer


def get_default_checkpointer() -> Any:
    """Return the active checkpointer: configured Postgres saver or fallback memory saver."""
    global _CONFIGURED_CHECKPOINTER
    if _CONFIGURED_CHECKPOINTER is not None:
        return _CONFIGURED_CHECKPOINTER
    return get_memory_checkpointer()


@asynccontextmanager
async def get_postgres_checkpointer(
    dsn: str | None = None,
    *,
    setup_tables: bool = True,
) -> AsyncIterator[Any]:
    """Async context manager yielding an initialized AsyncPostgresSaver checkpointer."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conn_dsn = dsn or get_checkpointer_dsn()
    async with AsyncPostgresSaver.from_conn_string(conn_dsn) as saver:
        if setup_tables:
            try:
                await saver.setup()
            except Exception as exc:  # noqa: BLE001
                logger.debug("postgres_checkpointer_setup_notice", extra={"error": str(exc)})
        yield saver


async def create_default_checkpointer(
    *,
    force_memory: bool = False,
) -> Any:
    """Resolve the default checkpointer for the current environment.

    Returns MemorySaver in TESTING or when force_memory is True.
    In non-TESTING environments, returns the configured default checkpointer.
    If no checkpointer is configured in PRODUCTION, raises a fail-loud RuntimeError.
    """
    if force_memory or settings.environment is Environment.TESTING:
        return get_memory_checkpointer()

    global _CONFIGURED_CHECKPOINTER
    if _CONFIGURED_CHECKPOINTER is not None:
        return _CONFIGURED_CHECKPOINTER

    if settings.environment not in (Environment.DEVELOPMENT, Environment.TESTING):
        raise RuntimeError(
            "AsyncPostgresSaver checkpointer is required in production but has not been configured in lifespan. "
            "Ensure PostgreSQL is reachable and DATABASE__URL is configured."
        )

    return get_memory_checkpointer()


async def configure_checkpointer_lifespan(logger: Any) -> Any:
    """Wire PostgreSQL AsyncPostgresSaver checkpointer for production / dev (spec P18-03, §18A.5)."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    if settings.environment is Environment.TESTING:
        return None

    conn_dsn = get_checkpointer_dsn()
    if "sqlite" in conn_dsn:
        if settings.environment not in (Environment.DEVELOPMENT, Environment.TESTING):
            raise RuntimeError(
                f"PostgreSQL is required for checkpointer in {settings.environment.value}. "
                "SQLite is not supported for durable production checkpointing."
            )
        return None

    try:
        cm = AsyncPostgresSaver.from_conn_string(conn_dsn)
        saver = await cm.__aenter__()
        try:
            await saver.setup()
        except Exception as exc:  # noqa: BLE001
            logger.debug("checkpointer.setup_notice", error=str(exc))
        set_default_checkpointer(saver)
        logger.info("checkpointer.postgres_configured")
        return cm
    except Exception as exc:  # noqa: BLE001 - postgres saver setup can raise driver errors
        if settings.environment not in (Environment.DEVELOPMENT, Environment.TESTING):
            raise RuntimeError(
                f"Failed to connect to PostgreSQL for checkpointer in {settings.environment.value}: {exc}"
            ) from exc
        logger.warning("checkpointer.postgres_fallback_to_memory", error=str(exc))
        return None


async def close_checkpointer_lifespan(cm: Any, logger: Any) -> None:
    """Tear down checkpointer connection on application shutdown."""
    if cm is not None:
        try:
            await cm.__aexit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("checkpointer_cleanup_notice", error=str(exc))
        set_default_checkpointer(None)


__all__ = [
    "close_checkpointer_lifespan",
    "configure_checkpointer_lifespan",
    "create_default_checkpointer",
    "get_checkpointer_dsn",
    "get_default_checkpointer",
    "get_memory_checkpointer",
    "get_postgres_checkpointer",
    "reset_memory_checkpointer",
    "set_default_checkpointer",
]
