"""Embedding service for the ingestion pipeline (spec P9D-1).

The service wraps a synchronous backend (local transformers model or a test
double), runs it off the event loop, batches inputs, retries transient engine
failures with bounded backoff, and validates every returned vector against
the configured dimensions — incompatible models/dimensions fail loudly and
never silently mix.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class DimensionMismatchError(ValueError):
    """Raised when a backend returns vectors inconsistent with the contract."""


class NonFiniteVectorError(ValueError):
    """Raised when a backend returns NaN/inf components (never retried)."""


class EmbeddingEngineError(RuntimeError):
    """Raised after bounded retries when the backend keeps failing."""


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Synchronous embedding function (model inference happens here)."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class EmbeddingContract:
    model_name: str
    dimensions: int


class LocalEmbeddingService:
    """Batches, retries, thread-offloads and dimension-validates embeddings."""

    def __init__(
        self,
        backend: EmbeddingBackend,
        *,
        contract: EmbeddingContract,
        batch_size: int = 64,
        max_attempts: int = 3,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._backend = backend
        self._contract = contract
        self._batch_size = batch_size
        self._max_attempts = max_attempts

    @property
    def contract(self) -> EmbeddingContract:
        return self._contract

    async def embed_documents(
        self,
        texts: list[str],
        *,
        batch_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(await self._embed_batch(batch))
            if batch_callback is not None:
                try:
                    res = batch_callback()
                    if inspect.isawaitable(res):
                        await res
                except Exception:  # noqa: BLE001 - callback isolation must not abort embedding
                    logger.debug("embedding_batch_callback_failed", exc_info=True)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        (vector,) = await self._embed_batch([text])
        return vector

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                vectors = await asyncio.to_thread(self._backend.embed, batch)
                self._validate(vectors, expected=len(batch))
                return vectors
            except (DimensionMismatchError, NonFiniteVectorError):
                raise  # configuration/data error: never retry, never mask
            except Exception as exc:  # noqa: BLE001 - transient engine failures only
                last_error = exc
                logger.warning(
                    "embedding_backend_retry",
                    extra={"attempt": attempt, "error": str(exc)},
                )
                if attempt < self._max_attempts:
                    await asyncio.sleep(0.05 * attempt)
        raise EmbeddingEngineError(
            f"embedding backend failed after {self._max_attempts} attempts: {last_error}"
        ) from last_error

    def _validate(self, vectors: list[list[float]], *, expected: int) -> None:
        if len(vectors) != expected:
            raise DimensionMismatchError(
                f"embedding backend returned {len(vectors)} vectors for {expected} inputs "
                f"(model={self._contract.model_name})"
            )
        for index, vector in enumerate(vectors):
            if len(vector) != self._contract.dimensions:
                raise DimensionMismatchError(
                    f"embedding dimension mismatch at vector {index}: got {len(vector)}, "
                    f"expected {self._contract.dimensions} (model={self._contract.model_name}); "
                    "a model change requires an explicit reindex/migration"
                )
            if not all(math.isfinite(value) for value in vector):
                raise NonFiniteVectorError(
                    f"embedding vector {index} contains NaN/inf values "
                    f"(model={self._contract.model_name}); the backend output is unusable"
                )


class TransformersPoolingBackend:
    """Offline backend for the local Vietnamese embedding snapshot.

    Loads ``AutoModel`` from ``EMBEDDING__LOCAL_PATH`` with
    ``local_files_only=True``; imports torch/transformers lazily so the rest
    of the runtime never touches them.

    Pooling follows the snapshot's ``1_Pooling/config.json``, which sets
    ``pooling_mode_cls_token=true`` (mean pooling FALSE): the sentence
    embedding is the first [CLS] token's hidden state, L2-normalized.
    Verified against the shipped artifact 2026-08-24 (review H1). If the
    snapshot is ever replaced, re-read its pooling config before ingesting.
    """

    def __init__(
        self, local_path: str, *, max_length: int = 512, device: str | None = None
    ) -> None:
        self._local_path = local_path
        self._max_length = max_length
        self._device = device
        self._tokenizer: Any = None
        self._model: Any = None

    def _ensure_loaded(self) -> tuple[Any, Any]:
        if self._model is None:
            import torch
            from transformers import AutoModel, AutoTokenizer

            if self._device is None:
                self._device = "cuda" if torch.cuda.is_available() else "cpu"

            self._tokenizer = AutoTokenizer.from_pretrained(self._local_path, local_files_only=True)
            self._model = AutoModel.from_pretrained(self._local_path, local_files_only=True)
            self._model.to(self._device)
            self._model.eval()
        assert self._tokenizer is not None and self._model is not None
        return self._tokenizer, self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        import torch

        tokenizer, model = self._ensure_loaded()
        encoded = tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self._max_length,
            return_tensors="pt",
        )
        if self._device != "cpu":
            encoded = {k: v.to(self._device) for k, v in encoded.items()}
        with torch.no_grad():
            output = model(**encoded)
        # CLS-token pooling per the snapshot's 1_Pooling config (review H1).
        cls_embeddings = output.last_hidden_state[:, 0, :]
        normalized = torch.nn.functional.normalize(cls_embeddings, p=2, dim=1)
        return normalized.cpu().tolist()
