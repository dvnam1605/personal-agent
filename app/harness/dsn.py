"""Checkpointer DSN derivation (spec P11-00, MASTER_PLAN §18A.6).

``langgraph-checkpoint-postgres`` speaks psycopg3 while the application data
layer uses asyncpg via SQLAlchemy. Both drivers coexist deliberately; this
helper derives the checkpointer DSN from ``DATABASE__URL`` by stripping the
SQLAlchemy driver marker.
"""

from __future__ import annotations

from app.domain.errors import ConfigurationError

_DRIVER_MARKERS = ("+asyncpg", "+psycopg", "+psycopg2")


def derive_checkpointer_dsn(database_url: str) -> str:
    """Derive a psycopg3 DSN from the SQLAlchemy database URL.

    ``postgresql+asyncpg://u:p@host:5432/db`` becomes
    ``postgresql://u:p@host:5432/db``. Anything that is not PostgreSQL after
    stripping is rejected: the checkpointer must never silently target a
    different engine.
    """
    url = (database_url or "").strip()
    if not url:
        raise ConfigurationError(
            "Cannot derive checkpointer DSN from an empty DATABASE__URL.",
            details={"database_url": "(empty)"},
        )
    # Strip driver markers from the scheme only, so credentials that happen to
    # contain a marker substring are never corrupted.
    scheme, sep, rest = url.partition("://")
    if sep:
        for marker in _DRIVER_MARKERS:
            scheme = scheme.replace(marker, "")
        url = f"{scheme}://{rest}"
    if not url.startswith("postgresql://"):
        raise ConfigurationError(
            "Checkpointer DSN must be PostgreSQL; refusing to derive from a "
            "non-postgres DATABASE__URL.",
            details={"database_url": url},
        )
    return url
