"""The orchestrator passes a heartbeat to embedding only when the service takes one."""

from __future__ import annotations

from typing import Any

from app.domain.models.ingestion import IngestionStatus
from app.services.ingestion.orchestrator import IngestionOrchestrator, _accepts_batch_callback
from tests.unit.services.ingestion.test_ingestion_orchestrator import (
    MD_FIXTURE,
    InMemoryRepository,
    NullTx,
    make_source,
)


class PlainEmbedding:
    """No ``batch_callback`` parameter, like older services and test doubles."""

    def __init__(self) -> None:
        self.calls = 0

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[0.1] * 8 for _ in texts]


class BrokenEmbedding:
    """Supports the callback but fails inside with a ``TypeError``."""

    def __init__(self) -> None:
        self.calls = 0

    async def embed_documents(
        self, texts: list[str], *, batch_callback: Any = None
    ) -> list[list[float]]:
        self.calls += 1
        raise TypeError("bad vector type")


async def _heartbeat(job_id: str) -> None:
    return None


def _orchestrator(embedding: Any) -> IngestionOrchestrator:
    return IngestionOrchestrator(
        repository=InMemoryRepository(),  # type: ignore[arg-type]
        transaction=NullTx(),  # type: ignore[arg-type]
        embedding=embedding,
        heartbeat_callback=_heartbeat,
    )


def test_accepts_batch_callback_detects_signature() -> None:
    assert not _accepts_batch_callback(PlainEmbedding())
    assert _accepts_batch_callback(BrokenEmbedding())


async def test_service_without_callback_is_called_once() -> None:
    embedding = PlainEmbedding()
    obs = await _orchestrator(embedding).ingest_source(make_source(), MD_FIXTURE.read_bytes())
    assert obs.status is IngestionStatus.COMPLETED
    assert embedding.calls == 1


async def test_type_error_inside_embedding_is_not_retried() -> None:
    embedding = BrokenEmbedding()
    obs = await _orchestrator(embedding).ingest_source(make_source(), MD_FIXTURE.read_bytes())
    assert obs.status is IngestionStatus.FAILED
    assert embedding.calls == 1
