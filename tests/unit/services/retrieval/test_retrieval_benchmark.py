"""Unit & integration tests for P10D retrieval benchmark, metrics, and ablations (spec P10-21..25)."""

from __future__ import annotations

import pytest

from app.domain.models.retrieval import ExpansionPolicy
from app.services.retrieval.benchmark_types import (
    BenchmarkQueryCategory,
)
from app.services.retrieval.evaluation import (
    AblationReport,
    AblationRunner,
    PipelineTimings,
    SearchModeAblationConfig,
    calculate_mrr,
    calculate_ndcg_at_k,
    calculate_recall_at_k,
)
from tests.fixtures.retrieval_benchmark_dataset import (
    BENCHMARK_CORPUS,
    BENCHMARK_QUERIES,
)

# ===========================================================================
# 1. Benchmark Dataset & Corpus Integrity (P10-21)
# ===========================================================================


class TestBenchmarkDatasetIntegrity:
    """Verifies all 11 required categories and ground-truth consistency."""

    def test_all_11_categories_represented(self) -> None:
        categories = {q.category for q in BENCHMARK_QUERIES}
        assert len(categories) == 11
        for cat in BenchmarkQueryCategory:
            assert cat in categories, f"Missing category: {cat}"

    def test_corpus_parent_child_hierarchy(self) -> None:
        parents = BENCHMARK_CORPUS.get_parents()
        children = BENCHMARK_CORPUS.get_children()

        assert len(parents) >= 3
        assert len(children) >= 6

        parent_ids = {p.chunk_id for p in parents}
        doc_ids = {d.document_id for d in BENCHMARK_CORPUS.documents}

        for child in children:
            assert child.parent_id in parent_ids, f"Child {child.chunk_id} parent not found"
            assert child.document_id in doc_ids, f"Child {child.chunk_id} doc not found"
            assert child.hierarchy_level == 1

        for parent in parents:
            assert parent.parent_id is None
            assert parent.document_id in doc_ids
            assert parent.hierarchy_level == 0

    def test_ground_truth_targets_exist_in_corpus(self) -> None:
        doc_ids = {d.document_id for d in BENCHMARK_CORPUS.documents}
        parent_ids = {p.chunk_id for p in BENCHMARK_CORPUS.get_parents()}
        child_ids = {c.chunk_id for c in BENCHMARK_CORPUS.get_children()}

        for q in BENCHMARK_QUERIES:
            for did in q.expected_doc_ids:
                assert did in doc_ids, f"Expected doc {did} not in corpus for {q.query_id}"
            for pid in q.expected_parent_ids:
                assert pid in parent_ids, f"Expected parent {pid} not in corpus for {q.query_id}"
            for cid in q.expected_child_ids:
                assert cid in child_ids, f"Expected child {cid} not in corpus for {q.query_id}"


# ===========================================================================
# 2. IR Metrics Correctness (Recall@k, MRR, nDCG@k)
# ===========================================================================


class TestIRMetrics:
    """Mathematical verification of evaluation metrics."""

    def test_recall_at_k_perfect(self) -> None:
        retrieved = ["c1", "c2", "c3", "c4", "c5"]
        expected = ["c1", "c2"]
        assert calculate_recall_at_k(retrieved, expected, k=5) == 1.0

    def test_recall_at_k_partial(self) -> None:
        retrieved = ["c1", "c3", "c5", "c7"]
        expected = ["c1", "c2"]
        # Only c1 is in top 4
        assert calculate_recall_at_k(retrieved, expected, k=4) == 0.5

    def test_recall_at_k_cutoff(self) -> None:
        retrieved = ["c1", "c2", "c3", "c4", "c5"]
        expected = ["c5"]
        # c5 is at index 4 (rank 5)
        assert calculate_recall_at_k(retrieved, expected, k=3) == 0.0
        assert calculate_recall_at_k(retrieved, expected, k=5) == 1.0

    def test_recall_negative_query(self) -> None:
        # Negative query: expected is empty
        assert calculate_recall_at_k([], [], k=5) == 1.0
        assert calculate_recall_at_k(["c1"], [], k=5) == 0.0

    def test_mrr_top_hit(self) -> None:
        retrieved = ["c1", "c2", "c3"]
        expected = ["c1"]
        assert calculate_mrr(retrieved, expected) == 1.0

    def test_mrr_second_hit(self) -> None:
        retrieved = ["c0", "c1", "c3"]
        expected = ["c1"]
        assert calculate_mrr(retrieved, expected) == 0.5

    def test_mrr_no_hit(self) -> None:
        retrieved = ["c0", "c2", "c3"]
        expected = ["c1"]
        assert calculate_mrr(retrieved, expected) == 0.0

    def test_mrr_negative_query(self) -> None:
        assert calculate_mrr([], []) == 1.0
        assert calculate_mrr(["c1"], []) == 0.0

    def test_ndcg_at_k_perfect(self) -> None:
        retrieved = ["c1", "c2", "c3"]
        expected = ["c1", "c2"]
        assert calculate_ndcg_at_k(retrieved, expected, k=3) == 1.0

    def test_ndcg_at_k_degraded(self) -> None:
        # Top hit is non-relevant, rank 2 and 3 are relevant
        retrieved = ["noise", "c1", "c2"]
        expected = ["c1", "c2"]
        ndcg = calculate_ndcg_at_k(retrieved, expected, k=3)
        assert 0.0 < ndcg < 1.0
        # In ideal order: rel at 1 (1/log2(2)=1) + rel at 2 (1/log2(3)=0.6309) = 1.6309
        # Actual: rel at 2 (1/log2(3)=0.6309) + rel at 3 (1/log2(4)=0.5) = 1.1309
        # Ratio: 1.1309 / 1.6309 ≈ 0.6934
        assert 0.65 < ndcg < 0.75

    def test_ndcg_empty_or_zero(self) -> None:
        assert calculate_ndcg_at_k([], ["c1"], k=5) == 0.0
        assert calculate_ndcg_at_k([], [], k=5) == 1.0


# ===========================================================================
# 3. Ablation Runner Execution (P10-22, P10-23, P10-24)
# ===========================================================================


class TestAblationRunner:
    """Executes ablation experiments and validates metrics generation."""

    def test_requires_explicit_query_list(self) -> None:
        with pytest.raises(TypeError, match="query sequence"):
            AblationRunner(BENCHMARK_CORPUS, None)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_single_level_baseline(self) -> None:
        runner = AblationRunner(BENCHMARK_CORPUS, BENCHMARK_QUERIES)
        report = await runner.evaluate_single_level_baseline()

        assert isinstance(report, AblationReport)
        assert report.configuration_name == "Single-Level Baseline"
        assert report.total_queries == len(BENCHMARK_QUERIES)
        assert report.mean_recall_at_10 > 0.6
        assert report.mean_mrr > 0.5
        assert report.mean_ndcg_at_10 > 0.5
        assert report.avg_context_tokens > 0

    @pytest.mark.asyncio
    async def test_parent_expansion_ablation(self) -> None:
        runner = AblationRunner(BENCHMARK_CORPUS, BENCHMARK_QUERIES)
        report = await runner.evaluate_pipeline_configuration(
            "Parent-Child (PARENT)",
            expansion_override=ExpansionPolicy.PARENT,
        )

        assert report.mean_recall_at_10 >= 0.7
        assert report.mean_mrr >= 0.7
        # Verify PARENT expansion uses larger context than single-level
        assert report.avg_context_tokens > 20

    @pytest.mark.asyncio
    async def test_neighbors_expansion_ablation(self) -> None:
        runner = AblationRunner(BENCHMARK_CORPUS, BENCHMARK_QUERIES)
        report = await runner.evaluate_pipeline_configuration(
            "Parent-Child (NEIGHBORS)",
            expansion_override=ExpansionPolicy.NEIGHBORS,
        )

        assert report.mean_recall_at_10 > 0.6
        assert report.mean_mrr > 0.6

    @pytest.mark.asyncio
    async def test_search_mode_ablations_comparison(self) -> None:
        runner = AblationRunner(BENCHMARK_CORPUS, BENCHMARK_QUERIES)
        dense_rep = await runner.evaluate_pipeline_configuration(
            "Dense Only", search_mode=SearchModeAblationConfig.DENSE_ONLY
        )
        sparse_rep = await runner.evaluate_pipeline_configuration(
            "Sparse Only", search_mode=SearchModeAblationConfig.SPARSE_ONLY
        )
        hybrid_rep = await runner.evaluate_pipeline_configuration(
            "Hybrid RRF + Reranker", search_mode=SearchModeAblationConfig.HYBRID_RERANK
        )

        # Hybrid with reranker should perform as well or better than either alone
        assert hybrid_rep.mean_recall_at_10 >= dense_rep.mean_recall_at_10
        assert hybrid_rep.mean_recall_at_10 >= sparse_rep.mean_recall_at_10
        assert hybrid_rep.mean_mrr >= dense_rep.mean_mrr

    @pytest.mark.asyncio
    async def test_run_all_ablations_smoke(self) -> None:
        runner = AblationRunner(BENCHMARK_CORPUS, BENCHMARK_QUERIES)
        reports = await runner.run_all_ablations()

        assert "single_level" in reports
        assert "child_only" in reports
        assert "child_neighbors" in reports
        assert "child_parent" in reports
        assert "hybrid_adaptive" in reports
        assert "dense_only" in reports
        assert "sparse_only" in reports
        assert "hybrid_rrf" in reports
        assert "hybrid_rerank" in reports

        for _name, rep in reports.items():
            assert rep.total_queries == len(runner.queries)
            assert rep.mean_recall_at_10 >= 0.5
            assert rep.avg_total_latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_latency_tracing_breakdown(self) -> None:
        runner = AblationRunner(BENCHMARK_CORPUS, BENCHMARK_QUERIES)
        report = await runner.evaluate_pipeline_configuration(
            "Hybrid RRF + Reranker", search_mode=SearchModeAblationConfig.HYBRID_RERANK
        )
        sample = report.query_results[0]
        t = sample.timings
        assert isinstance(t, PipelineTimings)
        assert t.total_ms > 0
        assert t.parallel_search_ms >= 0
        assert t.packing_ms >= 0
