"""Pluggable child reranking (spec P10-07).

The contract mirrors the execution spec: rerankers receive the diversified
candidates plus a ``top_k`` budget and MUST return descending-relevance items
carrying ``rerank_model`` / ``rerank_score`` / ``rerank_rank`` provenance so
traces can always explain post-rerank ordering.

Production wiring is hybrid RRF (``HybridRetrievalService``, RRF k=60)
followed by :class:`ViRankerReranker` (``namdp-ptit/ViRanker`` cross-encoder,
ADR-0012); :class:`IdentityReranker` remains as the deterministic no-op
baseline for unit tests and ablation controls. Use
``app.services.retrieval.factory.build_retrieval_pipeline`` to build the
settings-driven stack.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.core.config import settings as app_settings
from app.domain.models.retrieval import RetrievedChunk

if TYPE_CHECKING:
    from app.core.config import RerankerSettings

logger = logging.getLogger(__name__)


@runtime_checkable
class Reranker(Protocol):
    """Async reranking seam between fusion and expansion (spec P10-07)."""

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]: ...


class IdentityReranker:
    """No-op reranker preserving fusion order; tags provenance bookkeeping."""

    model_name = "identity:v1"
    display_name = "identity:v1"

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        selected = candidates[: max(int(top_k), 0)]
        return [
            chunk.model_copy(
                update={
                    "rerank_model": self.model_name,
                    "pre_rerank_rank": position,
                    "rerank_score": float(chunk.score),
                    "rerank_rank": position,
                }
            )
            for position, chunk in enumerate(selected, start=1)
        ]


class ViRankerReranker:
    """Neural cross-encoder reranker using namdp-ptit/ViRanker (spec ADR-0012).

    Computes query-passage cross-attention scores directly over candidates,
    providing fine-grained Vietnamese relevance reranking. Sigmoid-normalized
    scores land in ``[0, 1]`` so they line up with
    ``RerankerSettings.threshold`` (default 0.3) and the sufficiency gate.

    The model loads lazily on first :meth:`rerank` so importing this module
    (and building pipelines/factories) never touches torch or disk.
    """

    def __init__(
        self,
        model_path_or_name: str = "namdp-ptit/ViRanker",
        batch_size: int = 16,
        max_length: int = 512,
        device: str | None = None,
        *,
        threshold: float = 0.0,
        model_name: str | None = None,
        allow_network_download: bool = False,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if max_length <= 0:
            raise ValueError("max_length must be positive")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be within [0.0, 1.0]")
        self._model_path_or_name = model_path_or_name
        self._model_name = model_name or "namdp-ptit/ViRanker"
        self._display_name = model_path_or_name
        self._batch_size = batch_size
        self._max_length = max_length
        self._device = device
        self._threshold = threshold
        self._allow_network_download = allow_network_download
        self._tokenizer: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._load_lock = threading.Lock()

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @classmethod
    def from_settings(
        cls,
        reranker_settings: RerankerSettings | None = None,
        **overrides: Any,
    ) -> ViRankerReranker:
        """Build from ``RerankerSettings`` (model/local_path/threshold)."""
        settings_to_use = reranker_settings or app_settings.reranker
        model_name = settings_to_use.model or "namdp-ptit/ViRanker"
        local_dir = settings_to_use.local_path
        if local_dir:
            from app.core.config import resolve_project_path
            path_or_name = str(resolve_project_path(local_dir))
        else:
            path_or_name = model_name
        kwargs: dict[str, Any] = {
            "model_path_or_name": path_or_name,
            "model_name": model_name,
            "threshold": settings_to_use.threshold,
            "allow_network_download": getattr(settings_to_use, "allow_network_download", False),
            **overrides,
        }
        return cls(**kwargs)

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            import torch
            from huggingface_hub import snapshot_download
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._torch = torch
            if self._device is None:
                self._device = "cuda" if torch.cuda.is_available() else "cpu"

            try:
                path = snapshot_download(self._model_path_or_name, local_files_only=True)
            except Exception as exc:  # noqa: BLE001 - huggingface_hub raises a wide error set
                if not self._allow_network_download:
                    raise RuntimeError(
                        f"Local snapshot for reranker '{self._model_path_or_name}' not found and "
                        "network download is disabled (allow_network_download=False)."
                    ) from exc
                # Offline-first: local snapshot missing or unreadable. Falling
                # back to a network download changes provenance and hangs on
                # air-gapped hosts — log loudly so it never happens silently.
                logger.warning(
                    "reranker_local_snapshot_miss_falling_back_to_download",
                    extra={"model": self._model_path_or_name, "error": str(exc)},
                )
                path = snapshot_download(self._model_path_or_name, local_files_only=False)

            self._tokenizer = AutoTokenizer.from_pretrained(path)
            self._model = AutoModelForSequenceClassification.from_pretrained(path)
            self._model.to(self._device)
            self._model.eval()

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        if not candidates or top_k <= 0:
            return []

        await asyncio.to_thread(self._ensure_loaded)
        assert self._tokenizer is not None and self._model is not None
        torch = self._torch
        if torch is None:
            import torch as torch_mod

            self._torch = torch_mod
            torch = torch_mod

        # Build pairs: (query, candidate content)
        pairs = [[query, c.content_raw] for c in candidates]

        def _compute_scores() -> list[float]:
            batch_scores_all: list[float] = []
            with torch.no_grad():
                for i in range(0, len(pairs), self._batch_size):
                    batch_pairs = pairs[i : i + self._batch_size]
                    inputs = self._tokenizer(
                        batch_pairs,
                        padding=True,
                        truncation=True,
                        max_length=self._max_length,
                        return_tensors="pt",
                    )
                    if self._device != "cpu":
                        inputs = {k: v.to(self._device) for k, v in inputs.items()}
                    logits = self._model(**inputs).logits.view(-1).float()
                    # Apply sigmoid to convert cross-encoder logits into normalized [0, 1] scores
                    batch_scores = torch.sigmoid(logits).cpu().tolist()
                    if isinstance(batch_scores, float):
                        batch_scores = [batch_scores]
                    batch_scores_all.extend(batch_scores)
            return batch_scores_all

        scores: list[float] = await asyncio.to_thread(_compute_scores)

        # Pair each candidate with its neural score and original rank
        scored_candidates = [
            (score, orig_rank, chunk)
            for orig_rank, (chunk, score) in enumerate(
                zip(candidates, scores, strict=True), start=1
            )
        ]

        # Sort descending by neural rerank score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        if self._threshold > 0.0:
            scored_candidates = [item for item in scored_candidates if item[0] >= self._threshold]
        selected = scored_candidates[:top_k]

        return [
            chunk.model_copy(
                update={
                    "rerank_model": self._model_name,
                    "pre_rerank_rank": orig_rank,
                    "rerank_score": float(score),
                    "rerank_rank": position,
                }
            )
            for position, (score, orig_rank, chunk) in enumerate(selected, start=1)
        ]
