"""Row-fetch abstraction for retrieval services (review M1/M4 fix).

Retrievers never open their own connections or hardcode credentials: they
depend on this tiny provider. The default implementation runs statements
through the project's shared async engine (settings-driven URL, pooled).
Tests inject fakes that capture SQL and return canned rows.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from sqlalchemy import text

from app.infrastructure.db.session import get_engine


@runtime_checkable
class RowProvider(Protocol):
    """Single operation retrievers need: fetch rows for one statement."""

    async def fetch(self, sql: str) -> list[dict[str, Any]]: ...


class SqlAlchemyRowProvider:
    """Fetches rows through the shared async engine (settings-driven URL)."""

    def __init__(self, engine: Any | None = None) -> None:
        self._engine = engine

    def _resolve_engine(self) -> Any:
        if self._engine is None:
            self._engine = get_engine()
        return self._engine

    async def fetch(self, sql: str) -> list[dict[str, Any]]:
        engine = self._resolve_engine()
        async with engine.connect() as connection:
            result = await connection.execute(text(sql))
            return [dict(row) for row in result.mappings()]
