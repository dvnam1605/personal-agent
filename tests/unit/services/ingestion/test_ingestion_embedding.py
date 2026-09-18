"""Unit tests for the embedding service (spec P9D-1)."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
import torch
from transformers import AutoModel, AutoTokenizer

from app.services.ingestion.embedding import (
    DimensionMismatchError,
    EmbeddingContract,
    EmbeddingEngineError,
    LocalEmbeddingService,
    NonFiniteVectorError,
    TransformersPoolingBackend,
)

CONTRACT = EmbeddingContract(model_name="AITeamVN/Vietnamese_Embedding", dimensions=8)


class RecordingBackend:
    def __init__(self, *, dimensions: int = 8, fail_times: int = 0) -> None:
        self.dimensions = dimensions
        self.fail_times = fail_times
        self.calls: list[int] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient engine hiccup")
        return [[float(len(text) % 8)] * self.dimensions for text in texts]


class TestBatching:
    async def test_batches_split_by_batch_size(self) -> None:
        backend = RecordingBackend()
        service = LocalEmbeddingService(backend, contract=CONTRACT, batch_size=3)
        vectors = await service.embed_documents([f"t{i}" for i in range(7)])
        assert len(vectors) == 7
        assert backend.calls == [3, 3, 1]

    async def test_empty_input_short_circuits(self) -> None:
        backend = RecordingBackend()
        service = LocalEmbeddingService(backend, contract=CONTRACT)
        assert await service.embed_documents([]) == []
        assert backend.calls == []

    async def test_embed_query_returns_single_vector(self) -> None:
        service = LocalEmbeddingService(RecordingBackend(), contract=CONTRACT)
        vector = await service.embed_query("hello")
        assert len(vector) == 8


class TestDimensionValidation:
    async def test_wrong_dimension_fails_loudly_without_retry(self) -> None:
        backend = RecordingBackend(dimensions=4)
        service = LocalEmbeddingService(backend, contract=CONTRACT, max_attempts=5)
        with pytest.raises(DimensionMismatchError, match="expected 8"):
            await service.embed_documents(["a"])
        assert backend.calls == [1]  # no retries for configuration errors

    async def test_vector_count_mismatch_is_typed(self) -> None:
        class WrongCount:
            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[0.0] * 8] * (len(texts) - 1 or 1 + 1)  # always wrong count

        service = LocalEmbeddingService(WrongCount(), contract=CONTRACT)
        with pytest.raises(DimensionMismatchError, match="vectors for"):
            await service.embed_documents(["a", "b"])


class TestFiniteValidation:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    async def test_non_finite_vector_fails_loudly_without_retry(self, bad: float) -> None:
        class BadValueBackend:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def embed(self, texts: list[str]) -> list[list[float]]:
                self.calls.append(len(texts))
                return [[bad] * 8 for _ in texts]

        backend = BadValueBackend()
        service = LocalEmbeddingService(backend, contract=CONTRACT, max_attempts=4)
        with pytest.raises(NonFiniteVectorError, match="NaN/inf"):
            await service.embed_documents(["x"])
        assert backend.calls == [1]  # data error: never retried


class TestRetryPolicy:
    async def test_transient_failure_retried_then_succeeds(self) -> None:
        backend = RecordingBackend(fail_times=2)
        service = LocalEmbeddingService(backend, contract=CONTRACT, max_attempts=3)
        vectors = await service.embed_documents(["retry me"])
        assert len(vectors[0]) == 8
        assert len(backend.calls) == 3

    async def test_exhausted_retries_raise_engine_error(self) -> None:
        backend = RecordingBackend(fail_times=10)
        service = LocalEmbeddingService(backend, contract=CONTRACT, max_attempts=2)
        with pytest.raises(EmbeddingEngineError, match="after 2 attempts"):
            await service.embed_documents(["doomed"])

    async def test_invalid_construction_rejected(self) -> None:
        with pytest.raises(ValueError):
            LocalEmbeddingService(RecordingBackend(), contract=CONTRACT, batch_size=0)


class _SlowMoveFakeModel:
    """Mimics a transformers model whose .to(device) copy takes observable time."""

    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self._entered = entered
        self._release = release
        self.moved = False

    def to(self, device: object) -> _SlowMoveFakeModel:
        self._entered.set()
        assert self._release.wait(timeout=10), "loader thread timed out waiting for release"
        self.moved = True
        return self

    def eval(self) -> _SlowMoveFakeModel:
        return self

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
        if not self.moved:
            raise AssertionError("forward on half-moved model (cuda/cpu device mismatch)")
        batch = 1
        return SimpleNamespace(last_hidden_state=torch.ones(batch, 4, 8))


class _FakeTokenizer:
    def __call__(self, texts: list[str], **kwargs: Any) -> dict[str, Any]:
        count = len(texts)
        return {
            "input_ids": torch.zeros(count, 4, dtype=torch.long),
            "attention_mask": torch.ones(count, 4, dtype=torch.long),
        }


class TestConcurrentLoad:
    """embed() runs via asyncio.to_thread: concurrent first calls share one backend."""

    def _install_fakes(self, monkeypatch: pytest.MonkeyPatch, model: _SlowMoveFakeModel) -> None:
        monkeypatch.setattr(AutoModel, "from_pretrained", classmethod(lambda cls, *a, **k: model))
        monkeypatch.setattr(
            AutoTokenizer,
            "from_pretrained",
            classmethod(lambda cls, *a, **k: _FakeTokenizer()),
        )

    def test_ensure_loaded_publishes_fully_moved_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A caller arriving mid-load must block, never observe a half-moved model."""
        entered = threading.Event()
        release = threading.Event()
        backend = TransformersPoolingBackend("dummy-path", device="cpu")
        self._install_fakes(monkeypatch, _SlowMoveFakeModel(entered, release))

        errors: list[BaseException] = []

        def _load_in_background() -> None:
            try:
                backend.embed(["hello"])
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        worker = threading.Thread(target=_load_in_background, daemon=True)
        worker.start()
        assert entered.wait(timeout=10), "loader thread never reached .to(device)"
        # Release while this thread is (correctly) blocked inside _ensure_loaded.
        timer = threading.Timer(0.5, release.set)
        timer.start()
        try:
            _, observed = backend._ensure_loaded()
        finally:
            timer.cancel()
        assert observed.moved is True
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert errors == []

    def test_concurrent_embeds_all_succeed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Several threads embedding during cold start all get valid vectors."""
        entered = threading.Event()
        release = threading.Event()
        release.set()  # no artificial wait; the lock alone serializes loading
        backend = TransformersPoolingBackend("dummy-path", device="cpu")
        self._install_fakes(monkeypatch, _SlowMoveFakeModel(entered, release))

        results: list[list[list[float]]] = []
        errors: list[BaseException] = []

        def _embed() -> None:
            try:
                results.append(backend.embed([".cut"]))
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        workers = [threading.Thread(target=_embed, daemon=True) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
            assert not worker.is_alive()
        assert errors == []
        assert len(results) == 4
        for vectors in results:
            assert len(vectors) == 1
            assert len(vectors[0]) == 8
