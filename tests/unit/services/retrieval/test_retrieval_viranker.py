"""Tests for ViRanker wiring: settings adapter, threshold, factory, ablation injection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import RerankerSettings, Settings
from app.domain.models.retrieval import RetrievedChunk
from app.services.ingestion.embedding import EmbeddingContract, LocalEmbeddingService
from app.services.retrieval.evaluation import AblationRunner, SearchModeAblationConfig
from app.services.retrieval.factory import build_reranker, build_retrieval_pipeline
from app.services.retrieval.pipeline import RetrievalPipeline
from app.services.retrieval.rerank import IdentityReranker, ViRankerReranker
from app.services.retrieval.sufficiency import DEFAULT_MIN_SCORE_THRESHOLD
from tests.fixtures.retrieval_benchmark_dataset import BENCHMARK_CORPUS, BENCHMARK_QUERIES


def make_chunk(cid: str, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        parent_id="par-1",
        document_id="doc-1",
        content_raw=f"noi dung {cid}",
        score=score,
        retrieval_type="hybrid",
    )


class FakeProvider:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    async def fetch(self, sql: str) -> list[dict[str, Any]]:
        return list(self.rows)


class FixedHybrid:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks

    async def retrieve(self, query: Any) -> list[RetrievedChunk]:
        return self.chunks


class ReverseReranker:
    """Deterministic stand-in proving the pipeline honors injected rerankers."""

    model_name = "reverse:test"

    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        selected = list(reversed(candidates))[: max(int(top_k), 0)]
        return [
            chunk.model_copy(
                update={
                    "rerank_model": self.model_name,
                    "pre_rerank_rank": len(candidates) - position,
                    "rerank_score": 0.9,
                    "rerank_rank": position,
                }
            )
            for position, chunk in enumerate(selected, start=1)
        ]


class _FakeTokenizer:
    def __call__(self, pairs: list[list[str]], **kwargs: Any) -> dict[str, Any]:
        return {"batch_size": len(pairs)}


class _QueueLogitsModel:
    """Pops canned logits per batch so batching logic is exercised for real."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = list(scores)

    def __call__(self, **kwargs: Any) -> Any:
        import torch

        count = int(kwargs["batch_size"])
        batch, self._scores = self._scores[:count], self._scores[count:]
        return SimpleNamespace(logits=torch.tensor(batch))


def _install_fake_viranker(
    monkeypatch: pytest.MonkeyPatch,
    scores: list[float],
    *,
    batch_size: int = 16,
    threshold: float = 0.0,
) -> ViRankerReranker:
    tokenizer = _FakeTokenizer()
    model = _QueueLogitsModel(scores)
    pytest.importorskip("torch")

    def _load(self: ViRankerReranker) -> None:
        import torch

        self._tokenizer = tokenizer  # type: ignore[assignment]
        self._model = model  # type: ignore[assignment]
        self._torch = torch
        self._device = "cpu"

    monkeypatch.setattr(ViRankerReranker, "_ensure_loaded", _load)
    return ViRankerReranker(batch_size=batch_size, threshold=threshold)


def _make_settings(**reranker_kw: Any) -> Settings:
    return Settings(reranker=RerankerSettings(**reranker_kw))


def _fake_embedding_service(settings_obj: Settings) -> LocalEmbeddingService:
    dimensions = settings_obj.embedding.dimensions

    class _FakeBackend:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * dimensions for _ in texts]

    return LocalEmbeddingService(
        backend=_FakeBackend(),
        contract=EmbeddingContract(model_name=settings_obj.embedding.model, dimensions=dimensions),
    )

    def test_prefers_local_path_and_threshold(self) -> None:
        from app.core.config import resolve_project_path

        expected_path = str(resolve_project_path("models/snap"))
        reranker = ViRankerReranker.from_settings(
            RerankerSettings(model="namdp-ptit/ViRanker", local_path="models/snap", threshold=0.42)
        )
        assert reranker._model_path_or_name == expected_path
        assert reranker._display_name == expected_path
        assert reranker._threshold == 0.42

    def test_defaults_to_hub_model_and_config_threshold(self) -> None:
        reranker = ViRankerReranker.from_settings(RerankerSettings())
        assert reranker._model_path_or_name == "namdp-ptit/ViRanker"
        assert reranker._threshold == RerankerSettings().threshold

    def test_invalid_threshold_rejected(self) -> None:
        with pytest.raises(ValueError):
            ViRankerReranker(threshold=1.5)
        with pytest.raises(ValueError):
            ViRankerReranker(batch_size=0)


class TestViRankerRerank:
    async def test_orders_by_neural_score_with_provenance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reranker = _install_fake_viranker(monkeypatch, [0.1, 2.0, 0.5])
        chunks = [make_chunk("c1"), make_chunk("c2"), make_chunk("c3")]
        out = await reranker.rerank("truy van", chunks, top_k=3)
        assert [c.chunk_id for c in out] == ["c2", "c3", "c1"]
        assert [c.pre_rerank_rank for c in out] == [2, 3, 1]
        assert [c.rerank_rank for c in out] == [1, 2, 3]
        assert all(c.rerank_model == "namdp-ptit/ViRanker" for c in out)
        assert out[0].rerank_score is not None and 0.0 < out[0].rerank_score < 1.0

    async def test_threshold_filters_low_scores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # sigmoid(2.0)~0.88, sigmoid(-2.0)~0.12, sigmoid(0.0)=0.5
        reranker = _install_fake_viranker(monkeypatch, [2.0, -2.0, 0.0], threshold=0.6)
        chunks = [make_chunk("c1"), make_chunk("c2"), make_chunk("c3")]
        out = await reranker.rerank("truy van", chunks, top_k=5)
        assert [c.chunk_id for c in out] == ["c1"]

    async def test_top_k_and_empty_edges(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reranker = _install_fake_viranker(monkeypatch, [0.1, 2.0, 0.5])
        chunks = [make_chunk("c1"), make_chunk("c2"), make_chunk("c3")]
        out = await reranker.rerank("truy van", chunks, top_k=1)
        assert [c.chunk_id for c in out] == ["c2"]
        assert await reranker.rerank("truy van", [], top_k=5) == []
        assert await reranker.rerank("truy van", chunks, top_k=0) == []

    async def test_small_batches_score_all_candidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reranker = _install_fake_viranker(monkeypatch, [0.1, 2.0, 1.0], batch_size=2)
        chunks = [make_chunk("c1"), make_chunk("c2"), make_chunk("c3")]
        out = await reranker.rerank("truy van", chunks, top_k=3)
        assert [c.chunk_id for c in out] == ["c2", "c3", "c1"]


class TestRetrievalFactory:
    def test_wires_viranker_with_calibrated_threshold(self) -> None:
        settings_obj = _make_settings(threshold=0.35)
        pipeline = build_retrieval_pipeline(
            settings_obj,
            provider=FakeProvider(),
            embedding_service=_fake_embedding_service(settings_obj),
        )
        assert isinstance(pipeline, RetrievalPipeline)
        assert isinstance(pipeline._reranker, ViRankerReranker)
        assert pipeline._reranker._threshold == 0.35
        assert pipeline._sufficiency._min_score == 0.35

    def test_identity_when_viranker_disabled(self) -> None:
        settings_obj = _make_settings(threshold=0.35)
        pipeline = build_retrieval_pipeline(
            settings_obj,
            provider=FakeProvider(),
            embedding_service=_fake_embedding_service(settings_obj),
            use_viranker=False,
        )
        assert isinstance(pipeline._reranker, IdentityReranker)
        assert pipeline._sufficiency._min_score == DEFAULT_MIN_SCORE_THRESHOLD

    def test_explicit_reranker_wins_over_flag(self) -> None:
        settings_obj = _make_settings()
        pipeline = build_retrieval_pipeline(
            settings_obj,
            provider=FakeProvider(),
            embedding_service=_fake_embedding_service(settings_obj),
            reranker=IdentityReranker(),
            use_viranker=True,
        )
        assert isinstance(pipeline._reranker, IdentityReranker)
        assert pipeline._sufficiency._min_score == DEFAULT_MIN_SCORE_THRESHOLD

    def test_build_reranker_helper(self) -> None:
        assert isinstance(build_reranker(use_viranker=False), IdentityReranker)
        assert isinstance(build_reranker(_make_settings(), use_viranker=True), ViRankerReranker)


class TestPipelineRerankerInjection:
    async def test_pipeline_honors_injected_reranker_order(self) -> None:
        from app.domain.models.retrieval import RetrievalQuery

        seq = [make_chunk("c1", 0.9), make_chunk("c2", 0.5), make_chunk("c3", 0.3)]
        pipeline = RetrievalPipeline(FixedHybrid(seq), FakeProvider(), reranker=ReverseReranker())
        bundle = await pipeline.run(
            RetrievalQuery(original_query="van ban tieng Viet", search_query="van ban tieng Viet")
        )
        assert [item.primary_chunk_id for item in bundle.items] == ["c3", "c2", "c1"]

    async def test_hybrid_rerank_ablation_accepts_real_reranker(self) -> None:
        # A reversing reranker must degrade recall vs the fusion order: this
        # proves the HYBRID_RERANK slot actually consults the injected
        # reranker instead of silently keeping Identity order.
        runner = AblationRunner(BENCHMARK_CORPUS, BENCHMARK_QUERIES)
        injected = await runner.evaluate_pipeline_configuration(
            "Hybrid RRF + Injected Reranker",
            search_mode=SearchModeAblationConfig.HYBRID_RERANK,
            reranker=ReverseReranker(),
        )
        baseline = await runner.evaluate_pipeline_configuration(
            "Hybrid RRF + Reranker",
            search_mode=SearchModeAblationConfig.HYBRID_RERANK,
        )
        assert injected.total_queries == len(runner.queries)
        assert injected.mean_recall_at_10 < baseline.mean_recall_at_10

    async def test_viranker_network_download_disabled_fails_loud_on_miss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """M5: When allow_network_download is False and local snapshot is missing, fail loud."""
        from unittest.mock import MagicMock

        mock_download = MagicMock(side_effect=FileNotFoundError("Local snapshot miss"))
        monkeypatch.setattr("huggingface_hub.snapshot_download", mock_download)

        reranker = ViRankerReranker(
            model_path_or_name="nonexistent/model",
            allow_network_download=False,
        )
        with pytest.raises(RuntimeError, match="network download is disabled"):
            await reranker.rerank("query", [make_chunk("c1")], top_k=1)
        # Verify network download was never attempted
        assert mock_download.call_count == 1
        assert mock_download.call_args.kwargs.get("local_files_only") is True
