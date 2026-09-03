"""Pluggable child reranking (spec P10-07).

The contract mirrors the execution spec: rerankers receive the diversified
candidates plus a ``top_k`` budget and MUST return descending-relevance items
carrying ``rerank_model`` / ``rerank_score`` / ``rerank_rank`` provenance so
traces can always explain post-rerank ordering.

V1 ships only :class:`IdentityReranker` (no-op passthrough, ADR-0012's real
ViRanker cross-encoder adapter lands behind this protocol later).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models.retrieval import RetrievedChunk


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
    providing fine-grained Vietnamese relevance reranking.
    """

    model_name = "namdp-ptit/ViRanker"

    def __init__(
        self,
        model_path_or_name: str = "namdp-ptit/ViRanker",
        batch_size: int = 16,
        max_length: int = 512,
    ) -> None:
        self._model_path_or_name = model_path_or_name
        self._batch_size = batch_size
        self._max_length = max_length
        self._tokenizer = None
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is None:
            import torch
            from huggingface_hub import snapshot_download
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            try:
                path = snapshot_download(self._model_path_or_name, local_files_only=True)
            except Exception:
                path = snapshot_download(self._model_path_or_name, local_files_only=False)

            self._tokenizer = AutoTokenizer.from_pretrained(path)
            self._model = AutoModelForSequenceClassification.from_pretrained(path)
            self._model.eval()

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        if not candidates or top_k <= 0:
            return []

        import torch

        self._ensure_loaded()
        assert self._tokenizer is not None and self._model is not None

        # Build pairs: (query, candidate content)
        pairs = [[query, c.content_raw] for c in candidates]
        scores: list[float] = []

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
                logits = self._model(**inputs).logits.view(-1).float()
                # Apply sigmoid to convert cross-encoder logits into normalized [0, 1] scores
                batch_scores = torch.sigmoid(logits).tolist()
                if isinstance(batch_scores, float):
                    batch_scores = [batch_scores]
                scores.extend(batch_scores)

        # Pair each candidate with its neural score and original rank
        scored_candidates = [
            (score, orig_rank, chunk)
            for orig_rank, (chunk, score) in enumerate(zip(candidates, scores, strict=True), start=1)
        ]

        # Sort descending by neural rerank score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        selected = scored_candidates[:top_k]

        return [
            chunk.model_copy(
                update={
                    "rerank_model": self.model_name,
                    "pre_rerank_rank": orig_rank,
                    "rerank_score": float(score),
                    "rerank_rank": position,
                }
            )
            for position, (score, orig_rank, chunk) in enumerate(selected, start=1)
        ]

