"""Row-fetch abstraction for retrieval services (review M1/M4 fix).

Retrievers never open their own connections or hardcode credentials: they
depend on this tiny provider. The default implementation runs statements
through the project's shared async engine (settings-driven URL, pooled).
Tests inject fakes that capture SQL and return canned rows.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import text

from app.infrastructure.db.session import get_engine

# A lone ``:`` (not part of a ``::`` cast) would be parsed by ``text()`` as a
# bind parameter. Retrieval SQL carries every value as a validated literal, so
# user text such as ``"Điều 5 :khoản"`` must reach PostgreSQL verbatim.
_LONE_COLON = re.compile(r"(?<!:):(?!:)")


def escape_bind_colons(sql: str) -> str:
    """Escape lone colons so ``sqlalchemy.text`` never invents bind params."""
    return _LONE_COLON.sub(r"\\:", sql)


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
            result = await connection.execute(text(escape_bind_colons(sql)))
            return [dict(row) for row in result.mappings()]

    async def fetch_with_settings(
        self,
        sql: str,
        settings: Mapping[str, str],
        *,
        optional_settings: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Run ``sql`` after transaction-local ``set_config`` calls.

        ``optional_settings`` are applied only when the server knows the GUC
        (e.g. ``hnsw.iterative_scan`` needs pgvector >= 0.8), so older
        extensions degrade to the plain query instead of erroring.
        """
        engine = self._resolve_engine()
        async with engine.connect() as connection:
            if optional_settings:
                # Force the vector library to load so its GUCs are registered.
                await connection.execute(text("SELECT '[0]'::vector"))
            for name, value in settings.items():
                await connection.execute(
                    text("SELECT set_config(:name, :value, true)"),
                    {"name": name, "value": value},
                )
            for name, value in (optional_settings or {}).items():
                await connection.execute(
                    text(
                        "SELECT set_config(name, :value, true) FROM pg_settings WHERE name = :name"
                    ),
                    {"name": name, "value": value},
                )
            result = await connection.execute(text(escape_bind_colons(sql)))
            return [dict(row) for row in result.mappings()]
