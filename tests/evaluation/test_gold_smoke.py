"""Fast, offline checks of the RAG gold set (runs in CI: no DB, no LLM).

The live run (scripts/eval_rag.py) needs the backend; here we only check
that the gold file is well-formed, that the deterministic planner routes
each question the way the gold set expects, and that scoring is sound.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.domain.models.retrieval.metadata_filter import KnowledgeIntent
from app.services.documents.templates import is_generation_request, select_template
from app.services.evaluation.rag_eval import (
    LLM_DOWN_MARKER,
    compare,
    load_gold,
    score_content,
    score_metadata,
    score_negative,
    summarize,
)
from app.services.retrieval.metadata import KnowledgeQueryPlanner

GOLD = Path(__file__).parent / "gold" / "rag_gold_v1.json"
ITEMS = load_gold(GOLD, "all")
TODAY = date(2026, 10, 20)


def test_gold_file_is_well_formed() -> None:
    ids = [i["id"] for i in ITEMS]
    assert len(ids) == len(set(ids))
    assert {i["type"] for i in ITEMS} <= {"metadata", "content", "negative", "generation"}
    assert {i["split"] for i in ITEMS} == {"dev", "holdout"}
    for item in ITEMS:
        if item["type"] == "content":
            assert item.get("expected_doc_numbers") or item.get("expected_titles"), item["id"]
            assert item.get("gold_quotes"), item["id"]


@pytest.mark.parametrize(
    "item",
    [i for i in ITEMS if i.get("expected_intent")],
    ids=lambda i: i["id"],
)
def test_planner_routes_like_the_gold_set(item: dict) -> None:
    plan = KnowledgeQueryPlanner().plan(item["query"], today=TODAY)
    assert plan.intent is KnowledgeIntent(item["expected_intent"])


@pytest.mark.parametrize(
    "item", [i for i in ITEMS if i["type"] == "generation"], ids=lambda i: i["id"]
)
def test_generation_items_pick_their_template(item: dict) -> None:
    assert is_generation_request(item["query"])
    assert select_template(item["query"]).key == item["expected_template"]


def test_scoring_metadata_count_and_route() -> None:
    item = {"expected_count": 9}
    body = {
        "message": "Có **9** quyết định năm 2025.",
        "data": {"citations": [{"citation_type": "document"}]},
    }
    assert score_metadata(item, body) == {"count_exact": 1.0, "route_metadata": 1.0}


def test_scoring_content_uses_shown_evidence_and_skips_llm_metrics_when_down() -> None:
    item = {
        "expected_doc_numbers": ["80-QĐ/TNVN"],
        "gold_quotes": ["Cao Thị Hoa Hương"],
        "must_include": ["Hoa Hương"],
    }
    evidence = [
        {
            "prompt_text": '<retrieved_document id="E1" number="80/QĐ-TNVN">bà Cao Thị Hoa Hương</retrieved_document>',
            "cited": True,
        },
        {
            "prompt_text": '<retrieved_document id="E2" number="90/QĐ-TNVN">khác</retrieved_document>',
            "cited": True,
        },
    ]
    up = score_content(item, {"message": "Bà Cao Thị Hoa Hương [1]"}, evidence)
    assert up["retrieval_doc_hit"] == 1.0 and up["retrieval_quote_hit"] == 1.0
    assert up["citation_precision"] == 0.5 and up["answer_must_include"] == 1.0
    down = score_content(item, {"message": f"⚠️ {LLM_DOWN_MARKER}..."}, evidence)
    assert down["citation_precision"] is None and down["retrieval_doc_hit"] == 1.0


def test_scoring_negative() -> None:
    assert score_negative({}, {"data": {"sufficiency": "INSUFFICIENT"}}) == {"abstain_correct": 1.0}
    assert score_negative({}, {"message": LLM_DOWN_MARKER}) == {"abstain_correct": None}
    assert score_negative({}, {"data": {"sufficiency": "SUFFICIENT"}}) == {"abstain_correct": 0.0}


def test_summary_and_regression_compare() -> None:
    results = [
        {"metrics": {"count_exact": 1.0, "citation_precision": None}, "latency_s": 1.0},
        {"metrics": {"count_exact": 0.0}, "latency_s": 3.0},
    ]
    summary = summarize(results)
    assert summary["metrics"] == {"count_exact": 0.5}
    assert summary["skipped"] == {"citation_precision": 1}
    assert compare(summary, {"metrics": {"count_exact": 0.9}}, 0.05) == [
        "count_exact: 0.900 -> 0.500"
    ]
    assert compare(summary, {"metrics": {"count_exact": 0.52}}, 0.05) == []
