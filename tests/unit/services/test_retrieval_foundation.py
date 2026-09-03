"""Unit tests for P10A retrieval foundation (spec P10-02..P10-05)."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.domain.models.retrieval import (
    ExpansionPolicy,
    RetrievalMode,
    RetrievalQuery,
    RetrievedChunk,
)
from app.services.ingestion.embedding import EmbeddingContract, LocalEmbeddingService
from app.services.retrieval.dense import DenseRetrievalService
from app.services.retrieval.hybrid import HybridRetrievalService
from app.services.retrieval.sparse import SparseRetrievalService

CONTRACT = EmbeddingContract(model_name="test-model", dimensions=4)


class FakeProvider:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.sqls: list[str] = []

    async def fetch(self, sql: str) -> list[dict]:
        self.sqls.append(sql)
        return list(self.rows)


class DeterministicBackend:
    """Same shape as the P9D unit fake: one stable vector per text."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.extend(texts)
        return [[0.25, -0.25, 0.5, 1.0] for _ in texts]


def make_query(**overrides: object) -> RetrievalQuery:
    return RetrievalQuery(original_query="van ban tieng Viet", **overrides)


def chunk(
    chunk_id: str,
    score: float,
    kind: str,
    *,
    dense_rank: int | None = None,
    sparse_rank: int | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        parent_id="par-1",
        document_id="doc-1",
        content_raw=f"text {chunk_id}",
        score=score,
        retrieval_type=kind,
        dense_rank=dense_rank,
        sparse_rank=sparse_rank,
    )


class TestRetrievalQuery:
    def test_search_query_defaults_to_original(self) -> None:
        query = make_query()
        assert query.search_query == "van ban tieng Viet"
        assert query.mode is RetrievalMode.CORPUS_SEARCH
        assert query.top_k_dense == 20 and query.top_k_sparse == 20

    def test_document_modes_require_ids(self) -> None:
        with pytest.raises(ValueError, match="DOCUMENT_SEARCH"):
            make_query(mode=RetrievalMode.DOCUMENT_SEARCH)
        with pytest.raises(ValueError, match="COMPARE_DOCUMENTS"):
            make_query(mode=RetrievalMode.COMPARE_DOCUMENTS)
        ok = make_query(mode=RetrievalMode.DOCUMENT_SEARCH, document_ids=[str(uuid.uuid4())])
        assert ok.document_ids

    def test_metadata_lookup_rejected_at_contract(self) -> None:
        # P10-01: metadata questions take the Drive metadata path, never RAG.
        with pytest.raises(ValueError, match="METADATA_LOOKUP"):
            make_query(mode=RetrievalMode.METADATA_LOOKUP)

    def test_budget_bounds_enforced(self) -> None:
        with pytest.raises(ValueError):
            make_query(top_k_dense=0)
        with pytest.raises(ValueError):
            make_query(limit=1000)

    def test_expansion_policy_carried(self) -> None:
        assert make_query(expansion_policy=ExpansionPolicy.PARENT).expansion_policy is (
            ExpansionPolicy.PARENT
        )


class TestDenseRetrieval:
    async def test_sql_shape_and_ranking(self) -> None:
        provider = FakeProvider(
            [
                {
                    "chunk_id": uuid.uuid4(),
                    "parent_id": uuid.uuid4(),
                    "document_id": uuid.uuid4(),
                    "content_raw": "a",
                    "score": 0.11,
                },
                {
                    "chunk_id": uuid.uuid4(),
                    "parent_id": None,
                    "document_id": uuid.uuid4(),
                    "content_raw": "b",
                    "score": 0.22,
                },
            ]
        )
        service = DenseRetrievalService(LocalEmbeddingService(DeterministicBackend(), contract=CONTRACT), provider)
        results = await service.retrieve(make_query(requester_id=str(uuid.uuid4())))

        sql = provider.sqls[0]
        assert "<=>" in sql and "::vector" in sql
        assert "c.hierarchy_level = 1" in sql
        assert "d.is_active" in sql
        assert "d.user_id IS NULL" in sql  # ownership rule always present
        assert "LIMIT 20" in sql
        assert [r.dense_rank for r in results] == [1, 2]
        assert all(r.retrieval_type == "dense" for r in results)
        # vector literal built from floats only
        assert "[0.25,-0.25,0.5,1.0]" in sql.replace(" ", "")

    async def test_invalid_requester_rejected_before_sql(self) -> None:
        provider = FakeProvider()
        service = DenseRetrievalService(LocalEmbeddingService(DeterministicBackend(), contract=CONTRACT), provider)
        with pytest.raises(ValueError):
            await service.retrieve(make_query(requester_id="not-a-uuid"))
        assert provider.sqls == []

    async def test_document_mode_scopes_by_ids(self) -> None:
        doc_id = str(uuid.uuid4())
        provider = FakeProvider()
        service = DenseRetrievalService(LocalEmbeddingService(DeterministicBackend(), contract=CONTRACT), provider)
        await service.retrieve(make_query(mode=RetrievalMode.DOCUMENT_SEARCH, document_ids=[doc_id]))
        assert doc_id in provider.sqls[0]

    async def test_sql_selects_citation_anchors(self) -> None:
        provider = FakeProvider()
        service = DenseRetrievalService(LocalEmbeddingService(DeterministicBackend(), contract=CONTRACT), provider)
        await service.retrieve(make_query())
        sql = provider.sqls[0]
        assert "d.title AS document_title" in sql
        assert "c.chunk_index AS chunk_index" in sql
        assert "c.page_start AS page_start" in sql
        assert "c.page_end AS page_end" in sql
        assert "c.citation_label AS citation_label" in sql

    async def test_metadata_anchors_populated_from_row(self) -> None:
        row = {
            "chunk_id": uuid.uuid4(),
            "parent_id": None,
            "document_id": uuid.uuid4(),
            "content_raw": "noi dung",
            "document_title": "Luat Dat dai",
            "chunk_index": 3,
            "page_start": 12,
            "page_end": 12,
            "citation_label": "[Doc-1 p.12]",
            "score": 0.2,
        }
        provider = FakeProvider([row])
        service = DenseRetrievalService(LocalEmbeddingService(DeterministicBackend(), contract=CONTRACT), provider)
        (result,) = await service.retrieve(make_query())
        assert result.metadata["document_title"] == "Luat Dat dai"
        assert result.metadata["chunk_index"] == 3
        assert result.metadata["page_end"] == 12
        assert result.metadata["citation_label"] == "[Doc-1 p.12]"


class TestSparseRetrieval:
    async def test_websearch_tsquery_escapes_user_text(self) -> None:
        provider = FakeProvider()
        service = SparseRetrievalService(provider)
        await service.retrieve(make_query(search_query="hop dong O'Brien & phu luc"))

        sql = provider.sqls[0]
        assert "websearch_to_tsquery('public.vietnamese_simple', 'hop dong O''Brien & phu luc')" in sql
        assert "@@ c.search_vector" in sql or "c.search_vector @@" in sql
        assert "ts_rank_cd" in sql
        assert "ORDER BY score DESC" in sql
        assert "LIMIT 20" in sql

    async def test_ranks_follow_return_order(self) -> None:
        provider = FakeProvider([{"chunk_id": uuid.uuid4(), "parent_id": None, "document_id": uuid.uuid4(), "content_raw": "x", "score": 0.9}])
        service = SparseRetrievalService(provider)
        results = await service.retrieve(make_query())
        assert results[0].sparse_rank == 1

    async def test_metadata_anchors_populated_from_row(self) -> None:
        row = {
            "chunk_id": uuid.uuid4(),
            "parent_id": uuid.uuid4(),
            "document_id": uuid.uuid4(),
            "content_raw": "dieu 5",
            "document_title": "Luat Dat dai",
            "chunk_index": 5,
            "page_start": 4,
            "page_end": 4,
            "citation_label": "[Doc-2 p.4]",
            "score": 0.9,
        }
        provider = FakeProvider([row])
        service = SparseRetrievalService(provider)
        (result,) = await service.retrieve(make_query(search_query="luat dat"))
        assert result.metadata["chunk_index"] == 5
        assert result.metadata["page_start"] == 4
        assert result.metadata["citation_label"] == "[Doc-2 p.4]"


class TestHybridFusion:
    async def test_rrf_math_overlapping_and_disjoint(self) -> None:
        a, b, c = "chl-a", "chl-b", "chl-c"

        class Fixed:
            def __init__(self, chunks: list[RetrievedChunk]) -> None:
                self.chunks = chunks

            async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]:
                return self.chunks

        dense = Fixed([chunk(a, 0.1, "dense", dense_rank=1), chunk(b, 0.2, "dense", dense_rank=2)])
        sparse = Fixed([chunk(b, 5.0, "sparse", sparse_rank=1), chunk(c, 4.0, "sparse", sparse_rank=1)])
        service = HybridRetrievalService(dense, sparse, k=60)  # type: ignore[arg-type]

        results = await service.retrieve(make_query(limit=10))

        expected_b = 1 / 62 + 1 / 61
        expected_a_or_c = 1 / 61
        scores = {r.chunk_id: r.fusion_score for r in results}
        assert scores == {a: pytest.approx(expected_a_or_c), b: pytest.approx(expected_b), c: pytest.approx(expected_a_or_c)}
        assert [r.chunk_id for r in results][0] == b  # only overlap wins

        by_id = {r.chunk_id: r for r in results}
        assert by_id[a].metadata["retrieval_sources"] == ["dense"]
        assert by_id[b].metadata["retrieval_sources"] == ["dense", "sparse"]
        assert by_id[b].dense_rank == 2 and by_id[b].sparse_rank == 1

    async def test_full_fused_candidates_returned_for_pipeline(self) -> None:
        class Fixed:
            def __init__(self, chunks: list[RetrievedChunk]) -> None:
                self.chunks = chunks

            async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]:
                return self.chunks

        dense = Fixed([chunk(f"chl-{i}", 0.1 * i, "dense") for i in range(5)])
        sparse = Fixed([])
        service = HybridRetrievalService(dense, sparse, k=60)  # type: ignore[arg-type]
        results = await service.retrieve(make_query(limit=2))
        assert len(results) == 5


class TestParallelExecution:
    async def test_dense_and_sparse_run_concurrently(self) -> None:
        events: list[str] = []

        class Slow:
            def __init__(self, tag: str, provider: FakeProvider) -> None:
                self.tag = tag
                self.provider = provider

            async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]:
                events.append(f"{self.tag}:start")
                await asyncio.sleep(0.01)
                await self.provider.fetch("SELECT 1")
                events.append(f"{self.tag}:end")
                return []

        dense_provider, sparse_provider = FakeProvider(), FakeProvider()
        service = HybridRetrievalService(Slow("dense", dense_provider), Slow("sparse", sparse_provider))
        await service.retrieve(make_query())

        assert events.index("sparse:start") < events.index("dense:end"), events
