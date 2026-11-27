"""Entity catalogs are scoped to the documents their owner can read."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.services.retrieval.search import entity_catalog
from app.services.retrieval.search.entity_catalog import (
    DynamicEntityCatalog,
    get_entity_catalog,
    invalidate_entity_catalog,
)


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _Session:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    async def execute(self, statement: Any) -> _Result:
        self._sink.append(str(statement.compile(compile_kwargs={"literal_binds": True})))
        return _Result([([{"name": "Nguyễn Văn An", "kind": "signer"}], "Bộ Tài chính", None)])


def _factory(sink: list[str]) -> Any:
    @contextlib.asynccontextmanager
    async def session() -> AsyncIterator[_Session]:
        yield _Session(sink)

    return lambda: session()


@pytest.fixture(autouse=True)
def _fresh_catalogs() -> None:
    invalidate_entity_catalog()


async def test_shared_catalog_reads_only_shared_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql: list[str] = []
    monkeypatch.setattr(entity_catalog, "get_session_factory", lambda: _factory(sql))
    catalog = DynamicEntityCatalog()
    await catalog.refresh()
    assert "documents.user_id IS NULL" in sql[0]
    assert "documents.user_id =" not in sql[0]
    assert catalog.match_entities("ông nguyễn văn an ký")


async def test_owner_catalog_adds_the_owners_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    sql: list[str] = []
    monkeypatch.setattr(entity_catalog, "get_session_factory", lambda: _factory(sql))
    await DynamicEntityCatalog("user-a").refresh()
    assert "documents.user_id IS NULL OR documents.user_id = 'user-a'" in sql[0]


def test_one_cached_catalog_per_owner() -> None:
    assert get_entity_catalog("a") is get_entity_catalog("a")
    assert get_entity_catalog("a") is not get_entity_catalog("b")
    assert get_entity_catalog() is not get_entity_catalog("a")


def test_catalog_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entity_catalog, "_MAX_CATALOGS", 2)
    first = get_entity_catalog("a")
    get_entity_catalog("b")
    get_entity_catalog("c")
    assert get_entity_catalog("a") is not first
