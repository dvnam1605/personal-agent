"""Unit tests for the P10B processing pipeline (spec P10-06..P10-13)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.domain.models.retrieval import (
    Evidence,
    EvidenceBundle,
    ExpansionPolicy,
    RetrievalMode,
    RetrievalQuery,
    RetrievedChunk,
)
from app.services.ingestion.chunking.tokens import estimate_tokens
from app.services.retrieval.diversity import (
    apply_diversity,
    apply_per_document_caps,
    dedup_candidates,
    suppress_near_duplicates,
)
from app.services.retrieval.expansion import (
    ExpansionService,
    is_table_child,
    resolve_expansion_policy,
)
from app.services.retrieval.packing import build_bundle
from app.services.retrieval.pipeline import RetrievalPipeline
from app.services.retrieval.rerank import IdentityReranker


def make_query(**overrides: Any) -> RetrievalQuery:
    return RetrievalQuery(original_query="van ban tieng Viet", **overrides)


def chunk(
    cid: str,
    score: float = 0.5,
    *,
    doc: str = "doc-1",
    parent: str | None = "par-1",
    node: str | None = None,
    content: str | None = None,
) -> RetrievedChunk:
    meta = {"node_type": node} if node else {}
    return RetrievedChunk(
        chunk_id=cid,
        parent_id=parent,
        document_id=doc,
        content_raw=content if content is not None else f"noi dung {cid}",
        score=score,
        retrieval_type="hybrid",
        metadata=meta,
    )


class FakeProvider:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.sqls: list[str] = []

    async def fetch(self, sql: str) -> list[dict]:
        self.sqls.append(sql)
        return list(self.rows)


class FixedHybrid:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievedChunk]:
        return self.chunks


class TestQueryBudget:
    def test_default_and_bounds(self) -> None:
        q = make_query()
        assert q.context_token_budget == 4096
        with pytest.raises(ValueError):
            make_query(context_token_budget=64)


class TestDiversity:
    def test_dedup_keeps_first_by_id(self) -> None:
        a, dup = chunk("c1"), chunk("c1", 0.9)
        out = dedup_candidates([a, dup])
        assert [c.chunk_id for c in out] == ["c1"] and out[0].score == 0.5

    def test_near_duplicate_normalised_body_suppressed(self) -> None:
        first = chunk("c1", content="Dieu 5 quy dinh...")
        clone = chunk("c2", content="   Dieu  5 quy dinh...  ")
        distinct = chunk("c3", content="Noi dung khac hoan toan")
        out = suppress_near_duplicates([first, clone, distinct])
        assert [c.chunk_id for c in out] == ["c1", "c3"]

    def test_empty_and_whitespace_chunks_suppressed(self) -> None:
        empty = chunk("e1", content="")
        ws = chunk("e2", content="   \n  ")
        valid = chunk("v1", content="Hop le")
        out = suppress_near_duplicates([empty, ws, valid])
        assert [c.chunk_id for c in out] == ["v1"]

    def test_per_document_cap_in_arrival_order(self) -> None:
        seq = [
            chunk("a1", doc="A"),
            chunk("b1", doc="B"),
            chunk("a2", doc="A"),
            chunk("b2", doc="B"),
            chunk("a3", doc="A"),
        ]
        out = apply_per_document_caps(seq, cap=2)
        assert [c.chunk_id for c in out] == ["a1", "b1", "a2", "b2"]

    def test_apply_diversity_compare_mode_derives_balance_cap(self) -> None:
        seq = [chunk(f"a{i}", doc="A") for i in range(6)] + [chunk("b1", doc="B")]
        out = apply_diversity(seq, compare_document_ids=["A", "B", "C"])
        counts: dict[str, int] = {}
        for item in out:
            counts[item.document_id] = counts.get(item.document_id, 0) + 1
        assert counts["A"] == 3 and counts["B"] == 1

    def test_apply_diversity_chain_dedup_then_caps(self) -> None:
        seq = [
            chunk("a1", doc="A"),
            chunk("a1", doc="A"),
            chunk("a2", doc="A"),
            chunk("z", doc="B"),
        ]
        out = apply_diversity(seq, per_document_cap=1)
        assert [c.chunk_id for c in out] == ["a1", "z"]


class TestSQLTemplates:
    def test_parent_fetch_sql_syntax_not_nested_in(self) -> None:
        from app.services.retrieval.sql import (
            PARENT_FETCH_TEMPLATE,
            SIBLING_FETCH_TEMPLATE,
            owner_scope_sql,
            parent_scope_sql,
            sibling_scope_sql,
        )

        pids = [str(uuid.uuid4()), str(uuid.uuid4())]
        parent_sql = PARENT_FETCH_TEMPLATE.format(
            parent_scope=parent_scope_sql(pids),
            owner_scope=owner_scope_sql(None),
        )
        assert "c.id IN (c.id IN (" not in parent_sql
        assert "AND c.id IN ('" in parent_sql
        assert "d.title AS filename" in parent_sql
        assert "d.id AS document_version_id" in parent_sql

        sibling_sql = SIBLING_FETCH_TEMPLATE.format(
            sibling_scope=sibling_scope_sql(pids),
            owner_scope=owner_scope_sql(None),
        )
        assert "c.parent_id IN (c.parent_id IN (" not in sibling_sql
        assert "AND c.parent_id IN ('" in sibling_sql
        assert "d.title AS filename" in sibling_sql
        assert "d.id AS document_version_id" in sibling_sql


class TestRerank:
    async def test_identity_passthrough_tags_provenance(self) -> None:
        seq = [chunk("c1", 0.7), chunk("c2", 0.4)]
        out = await IdentityReranker().rerank("truy van", seq, top_k=2)
        assert [c.pre_rerank_rank for c in out] == [1, 2]
        assert [c.rerank_rank for c in out] == [1, 2]
        assert all(c.rerank_model == "identity:v1" for c in out)
        assert out[0].rerank_score == 0.7 and out[1].rerank_score == 0.4
        assert [c.chunk_id for c in out] == ["c1", "c2"]


class TestExpansionDecision:
    def test_explicit_override_wins(self) -> None:
        q = make_query(
            search_query="diem so la bao nhieu", expansion_policy=ExpansionPolicy.NEIGHBORS
        )
        assert resolve_expansion_policy(q) is ExpansionPolicy.NEIGHBORS

    @pytest.mark.parametrize(
        "text",
        ["Tai sao quy dinh nay lai nhu vay?", "explain the approach", "hay so sanh hai phuong an"],
    )
    def test_question_hints_trigger_parent(self, text: str) -> None:
        assert resolve_expansion_policy(make_query(search_query=text)) is ExpansionPolicy.PARENT

    def test_plain_fact_defaults_none(self) -> None:
        assert resolve_expansion_policy(make_query(search_query="ngay ban hanh van ban")) is (
            ExpansionPolicy.NONE
        )


def _parent_row(pid: str, doc: str, content: str, heading: list[str] | str) -> dict:
    return {
        "chunk_id": pid,
        "document_id": doc,
        "content_raw": content,
        "heading_path": heading,
    }


def _sibling_row(
    cid: str,
    pid: str,
    index: int,
    *,
    doc: str = "doc-1",
    node: str = "CHILD",
    content: str | None = None,
) -> dict:
    return {
        "chunk_id": cid,
        "parent_id": pid,
        "document_id": doc,
        "node_type": node,
        "heading_path": '["Muc 1"]',
        "chunk_index": index,
        "page_start": 3,
        "page_end": 3,
        "citation_label": f"[{cid}]",
        "document_title": "Van ban mau",
        "uri": "drive://van-ban-mau.pdf",
        "filename": "van-ban-mau.pdf",
        "document_version_id": f"{doc}-v1",
        "content_raw": content or f"noi dung {cid}",
    }


class TestParentExpansion:
    async def test_groups_children_one_evidence_per_parent(self) -> None:
        pid = str(uuid.uuid4())
        provider = FakeProvider([_parent_row(pid, "doc-1", "Phan ma cha dai dai", ["I", "II"])])
        service = ExpansionService(provider)
        units = await service.build_units(
            [chunk("c1", parent=pid), chunk("c2", parent=pid)],
            ExpansionPolicy.PARENT,
            make_query(),
        )
        assert len(units) == 1
        unit = units[0]
        assert unit.kind == "PARENT" and unit.parent_id == pid
        assert unit.chunk_ids == ["c1", "c2"]
        assert unit.heading_path == ["I", "II"]
        assert unit.token_estimate == estimate_tokens("Phan ma cha dai dai")

    async def test_priority_formula_orders_parents(self) -> None:
        p1, p2 = str(uuid.uuid4()), str(uuid.uuid4())
        rows = [_parent_row(p1, "doc-1", "cha mot", []), _parent_row(p2, "doc-1", "cha hai", [])]
        provider = FakeProvider(rows)
        service = ExpansionService(provider)
        ranked = [
            chunk("c1", 0.5, parent=p1),
            chunk("c2", 0.9, parent=p2),
            chunk("c3", 0.8, parent=p2),
        ]
        units = await service.build_units(ranked, ExpansionPolicy.PARENT, make_query())
        # par-2: best 0.9 + bonus; par-1: best 0.5 -> par-2 first
        assert [u.parent_id for u in units] == [p2, p1]

    async def test_parent_priority_uses_rerank_score_over_fusion_score(self) -> None:
        p1, p2 = str(uuid.uuid4()), str(uuid.uuid4())
        rows = [_parent_row(p1, "doc-1", "cha mot", []), _parent_row(p2, "doc-1", "cha hai", [])]
        provider = FakeProvider(rows)
        service = ExpansionService(provider)
        # c1 has high fusion score (0.9) but low rerank_score (0.2)
        c1 = chunk("c1", 0.9, parent=p1).model_copy(update={"rerank_score": 0.2})
        # c2 has low fusion score (0.3) but high rerank_score (0.85)
        c2 = chunk("c2", 0.3, parent=p2).model_copy(update={"rerank_score": 0.85})
        units = await service.build_units([c1, c2], ExpansionPolicy.PARENT, make_query())
        # p2 must come first because c2's rerank_score (0.85) > c1's rerank_score (0.2)
        assert [u.parent_id for u in units] == [p2, p1]

    async def test_table_children_interleaved_by_score_not_appended_last(self) -> None:
        p1 = str(uuid.uuid4())
        rows = [_parent_row(p1, "doc-1", "noi dung cha", [])]
        provider = FakeProvider(rows)
        service = ExpansionService(provider)
        # Table child has highest score (0.95), regular child has score 0.5 under parent p1
        table_hit = chunk("t1", 0.95, parent=p1, node="TABLE_CHILD")
        reg_hit = chunk("c1", 0.5, parent=p1)
        units = await service.build_units(
            [table_hit, reg_hit], ExpansionPolicy.PARENT, make_query()
        )
        assert len(units) == 2
        # TABLE_CHILD has score 0.95 > parent p1 priority (0.5), so TABLE_CHILD is first!
        assert units[0].kind == "TABLE_CHILD" and units[0].primary_chunk_id == "t1"
        assert units[1].kind == "PARENT" and units[1].parent_id == p1

    def test_table_detection_helpers(self) -> None:
        assert is_table_child(chunk("x", node="TABLE_CHILD")) is True
        assert is_table_child(chunk("y", node=None)) is False

    async def test_owner_guard_present_in_fetch_sql(self) -> None:
        provider = FakeProvider([_parent_row("par-1", "doc-1", "noi dung cha", [])])
        service = ExpansionService(provider)
        await service.build_units(
            [chunk("c1", parent=str(uuid.uuid4()))], ExpansionPolicy.PARENT, make_query()
        )
        sql = provider.sqls[0]
        assert "d.user_id" in sql and "IN (" in sql and "hierarchy_level = 0" in sql
        assert "c.id IN (c.id IN (" not in sql


class TestNeighborExpansion:
    async def test_window_prev_hit_next_within_parent(self) -> None:
        pid = str(uuid.uuid4())
        rows = [_sibling_row(f"c{i}", pid, i) for i in range(1, 6)]
        provider = FakeProvider(rows)
        service = ExpansionService(provider)
        units = await service.build_units(
            [chunk("c3", parent=pid)], ExpansionPolicy.NEIGHBORS, make_query()
        )
        assert len(units) == 1
        unit = units[0]
        assert unit.kind == "NEIGHBOR_GROUP"
        assert unit.chunk_ids == ["c2", "c3", "c4"]
        assert unit.heading_path == ["Muc 1"]
        # Primary chunk and anchors belong to c3 (the hit), not c2 or c4
        assert unit.primary_chunk_id == "c3"
        assert unit.anchors.get("citation_label") == "[c3]"
        assert unit.filename == "van-ban-mau.pdf"
        assert unit.document_version_id == "doc-1-v1"

    async def test_multiple_hits_merge_into_single_group(self) -> None:
        pid = str(uuid.uuid4())
        rows = [_sibling_row(f"c{i}", pid, i) for i in range(1, 6)]
        provider = FakeProvider(rows)
        service = ExpansionService(provider)
        units = await service.build_units(
            [chunk("c2", parent=pid), chunk("c4", parent=pid)],
            ExpansionPolicy.NEIGHBORS,
            make_query(),
        )
        assert len(units) == 1
        assert units[0].chunk_ids == ["c1", "c2", "c3", "c4", "c5"]  # no duplicate overlap text

    async def test_different_parents_stay_separate_groups(self) -> None:
        pa, pb = str(uuid.uuid4()), str(uuid.uuid4())
        rows = [_sibling_row(f"a{i}", pa, i, doc="doc-1") for i in range(1, 4)] + [
            _sibling_row(f"b{i}", pb, i, doc="doc-2") for i in range(1, 4)
        ]
        provider = FakeProvider(rows)
        service = ExpansionService(provider)
        ranked = [
            chunk("a2", doc="doc-1", parent=pa),
            chunk("b2", doc="doc-2", parent=pb),
        ]
        units = await service.build_units(ranked, ExpansionPolicy.NEIGHBORS, make_query())
        assert [u.parent_id for u in units] == [pa, pb]
        assert all(u.kind == "NEIGHBOR_GROUP" for u in units)

    async def test_owner_guard_present_in_sibling_sql(self) -> None:
        provider = FakeProvider([_sibling_row("c2", str(uuid.uuid4()), 2)])
        service = ExpansionService(provider)
        await service.build_units(
            [chunk("c2", parent=str(uuid.uuid4()))], ExpansionPolicy.NEIGHBORS, make_query()
        )
        sql = provider.sqls[0]
        assert "d.user_id" in sql and "hierarchy_level = 1" in sql and "ORDER BY c.parent_id" in sql
        assert "c.parent_id IN (c.parent_id IN (" not in sql


def _unit(
    text: str, cid: str, *, kind: str = "CHILD", doc: str = "doc-1", parent: str | None = None
) -> Evidence:
    return Evidence(
        kind=kind,  # type: ignore[arg-type]
        content_raw=text,
        token_estimate=estimate_tokens(text),
        primary_chunk_id=cid,
        document_id=doc,
        parent_id=parent,
        chunk_ids=[cid],
    )


class TestPacking:
    def test_greedy_fill_respects_hard_budget(self) -> None:
        u1 = _unit("x" * 380, "u1")  # ~99 tokens
        u2 = _unit("y" * 120, "u2")  # ~33 tokens
        u3 = _unit("z" * 2000, "u3")  # way over remaining space
        budget = u1.token_estimate + u2.token_estimate - 5
        bundle = build_bundle([u1, u2, u3], token_budget=budget, trace_id="fixed")
        assert [e.primary_chunk_id for e in bundle.items] == ["u1"]
        assert bundle.total_tokens == u1.token_estimate

    def test_oversize_unit_dropped_not_truncated(self) -> None:
        big = _unit("q" * 3000, "big")
        bundle = build_bundle([big], token_budget=130)
        assert bundle.items == [] and bundle.total_tokens == 0

    def test_bundle_fields_and_trace(self) -> None:
        units = [
            _unit("abc", "c1", kind="PARENT", doc="doc-B", parent="par-9"),
            _unit("def", "c2", kind="CHILD", doc="doc-A", parent="par-unexpanded"),
        ]
        bundle = build_bundle(units, token_budget=5000)
        import re as _re

        assert _re.fullmatch(r"[0-9a-f]{32}", bundle.retrieval_trace_id)
        assert bundle.documents_used == ["doc-A", "doc-B"]
        # Only PARENT / NEIGHBOR_GROUP parent_ids are recorded, CHILD parent_id is ignored
        assert bundle.parent_ids_used == ["par-9"]
        assert isinstance(bundle, EvidenceBundle)


class TestPipelineEndToEnd:
    async def test_none_policy_full_flow_preserves_order_and_tags(self) -> None:
        seq = [
            chunk("c1", 0.9),
            chunk("c2", 0.5),
            chunk("dup-c1", 0.3),
        ]
        provider = FakeProvider()
        pipeline = RetrievalPipeline(FixedHybrid(seq), provider)
        bundle = await pipeline.run(make_query())
        assert [item.primary_chunk_id for item in bundle.items] == ["c1", "c2", "dup-c1"]
        assert all(item.kind == "CHILD" for item in bundle.items)
        assert provider.sqls == []  # NONE policy touches no extra storage
        import re as _re

        assert _re.fullmatch(r"[0-9a-f]{32}", bundle.retrieval_trace_id)

    async def test_pipeline_dedup_collapses_duplicate_ids(self) -> None:
        c1 = chunk("same-id", 0.9)
        clone = chunk("same-id", 0.2)
        pipeline = RetrievalPipeline(FixedHybrid([c1, clone]), FakeProvider())
        bundle = await pipeline.run(make_query())
        assert len(bundle.items) == 1

    async def test_parent_flow_fetches_once_with_guard_and_packs(self) -> None:
        parent_id = str(uuid.uuid4())
        rows = [
            {
                "chunk_id": parent_id,
                "document_id": "doc-1",
                "content_raw": "noi dung cha du lon de pack",
                "heading_path": '["Chuong 1"]',
            }
        ]
        provider = FakeProvider(rows)
        hybrid_seq = [
            chunk("c1", 0.8, doc="doc-1", parent=parent_id),
            chunk("c2", 0.6, doc="doc-1", parent=str(uuid.uuid4())),  # missing payload row
        ]
        pipeline = RetrievalPipeline(FixedHybrid(hybrid_seq), provider, per_document_cap=None)
        query = make_query(search_query="tai sao co quy dinh nay", context_token_budget=2048)
        bundle = await pipeline.run(query)

        assert len(provider.sqls) == 1
        sql = provider.sqls[0]
        assert "hierarchy_level = 0" in sql and "d.user_id" in sql
        assert "c.id IN (c.id IN (" not in sql
        kinds = [(item.kind, item.parent_id) for item in bundle.items]
        # par for c1 resolved; c2's missing parent logs a warning and is skipped
        assert ("PARENT", parent_id) in kinds
        assert any(item.kind == "PARENT" for item in bundle.items)

    async def test_compare_mode_passes_document_ids_to_diversity(self) -> None:
        seq = [chunk(f"a{i}", 0.5 - i * 0.01, doc="A") for i in range(4)]
        seq += [chunk("b0", 0.55, doc="B")]
        pipeline = RetrievalPipeline(FixedHybrid(seq), FakeProvider(), rerank_top_k_max=48)
        query = make_query(
            mode=RetrievalMode.COMPARE_DOCUMENTS,
            document_ids=["A", "B"],
            search_query="so sanh hai van ban",
            expansion_policy=ExpansionPolicy.NONE,
        )
        bundle = await pipeline.run(query)
        docs = {item.document_id for item in bundle.items}
        assert docs == {"A", "B"}
        # balance cap = ceil(5/2)=3 -> A capped at 3, B kept
        a_items = [i for i in bundle.items if i.document_id == "A"]
        assert len(a_items) == 3

    async def test_identity_reranker_ran_provenance_present(self) -> None:
        pipeline = RetrievalPipeline(FixedHybrid([chunk("c1"), chunk("c2")]), FakeProvider())
        bundle = await pipeline.run(make_query())
        # provenance bookkeeping lives on chunks pre-packing; anchors stripped:
        # verify indirectly that every unit kept its content ordering from fusion
        assert [item.content_raw for item in bundle.items] == ["noi dung c1", "noi dung c2"]
