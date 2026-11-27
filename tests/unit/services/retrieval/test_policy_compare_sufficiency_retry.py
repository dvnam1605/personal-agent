"""Retrieval policy tests: compare-document diversity (P10-14), sufficiency checker (P10-16) and bounded retry (P10-17)."""

from __future__ import annotations

from app.domain.models.retrieval import (
    ExpansionPolicy,
    RetrievalMode,
)
from app.domain.models.retrieval.sufficiency import SufficiencyStatus, SufficiencyVerdict
from app.services.retrieval.evidence.compare_policy import enforce_compare_diversity
from app.services.retrieval.evidence.retry import (
    DEFAULT_MAX_ATTEMPTS,
    RetryPolicy,
    RetryStrategy,
    apply_retry_strategy,
)
from app.services.retrieval.evidence.sufficiency import SufficiencyChecker
from tests.unit.services.retrieval._policy_factories import (
    make_bundle,
    make_evidence,
    make_query,
)

# ===========================================================================
# P10-14: Compare-document policy
# ===========================================================================


class TestComparePolicyEnforcement:
    """P10-14 compare-document coverage audit."""

    def test_all_docs_covered(self) -> None:
        items = [make_evidence(doc="d1"), make_evidence(doc="d2")]
        bundle = make_bundle(items, docs=["d1", "d2"])
        result = enforce_compare_diversity(bundle, ["d1", "d2"])
        assert result.covered_documents == ["d1", "d2"]
        assert result.missing_documents == []
        assert result.balance_ratio == 1.0

    def test_one_doc_missing(self) -> None:
        items = [make_evidence(doc="d1"), make_evidence(doc="d1")]
        bundle = make_bundle(items, docs=["d1"])
        result = enforce_compare_diversity(bundle, ["d1", "d2"])
        assert result.covered_documents == ["d1"]
        assert result.missing_documents == ["d2"]
        assert result.balance_ratio == 0.0

    def test_imbalanced_coverage(self) -> None:
        items = [
            make_evidence(doc="d1"),
            make_evidence(doc="d1"),
            make_evidence(doc="d1"),
            make_evidence(doc="d2"),
        ]
        bundle = make_bundle(items, docs=["d1", "d2"])
        result = enforce_compare_diversity(bundle, ["d1", "d2"])
        assert result.covered_documents == ["d1", "d2"]
        assert result.missing_documents == []
        assert 0.3 < result.balance_ratio < 0.4  # 1/3

    def test_empty_document_ids_skips(self) -> None:
        items = [make_evidence(doc="d1")]
        bundle = make_bundle(items)
        result = enforce_compare_diversity(bundle, [])
        assert result.missing_documents == []
        assert result.balance_ratio == 1.0

    def test_empty_bundle(self) -> None:
        bundle = make_bundle([], docs=[])
        result = enforce_compare_diversity(bundle, ["d1", "d2"])
        assert result.missing_documents == ["d1", "d2"]
        assert result.balance_ratio == 0.0

    def test_three_docs_partial_missing(self) -> None:
        items = [make_evidence(doc="d1"), make_evidence(doc="d3")]
        bundle = make_bundle(items, docs=["d1", "d3"])
        result = enforce_compare_diversity(bundle, ["d1", "d2", "d3"])
        assert "d2" in result.missing_documents
        assert "d1" in result.covered_documents
        assert "d3" in result.covered_documents


# ===========================================================================
# P10-16: Sufficiency checker
# ===========================================================================


class TestSufficiencyChecker:
    """P10-16 deterministic sufficiency rules."""

    def test_empty_bundle_insufficient(self) -> None:
        checker = SufficiencyChecker()
        bundle = make_bundle([])
        query = make_query()
        verdict = checker.check(bundle, query)
        assert verdict.status is SufficiencyStatus.INSUFFICIENT
        assert "No evidence" in verdict.reason

    def test_below_min_items_insufficient(self) -> None:
        checker = SufficiencyChecker(min_evidence_items=3)
        items = [make_evidence(), make_evidence()]
        bundle = make_bundle(items)
        verdict = checker.check(bundle, make_query())
        assert verdict.status is SufficiencyStatus.INSUFFICIENT
        assert "2 item" in verdict.reason

    def test_compare_mode_missing_doc_partial(self) -> None:
        checker = SufficiencyChecker()
        items = [make_evidence(doc="d1")]
        bundle = make_bundle(items, docs=["d1"])
        query = make_query(
            mode=RetrievalMode.COMPARE_DOCUMENTS,
            document_ids=["d1", "d2"],
        )
        verdict = checker.check(bundle, query)
        assert verdict.status is SufficiencyStatus.PARTIAL
        assert "d2" in verdict.missing_document_ids

    def test_document_search_missing_doc_partial(self) -> None:
        checker = SufficiencyChecker()
        items = [make_evidence(doc="other")]
        bundle = make_bundle(items, docs=["other"])
        query = make_query(
            mode=RetrievalMode.DOCUMENT_SEARCH,
            document_ids=["target-doc"],
        )
        verdict = checker.check(bundle, query)
        assert verdict.status is SufficiencyStatus.PARTIAL
        assert "target-doc" in verdict.missing_document_ids

    def test_all_below_threshold_insufficient(self) -> None:
        checker = SufficiencyChecker(min_score_threshold=0.5)
        items = [
            make_evidence(anchors={"rerank_score": 0.1}),
            make_evidence(anchors={"rerank_score": 0.2}),
        ]
        bundle = make_bundle(items)
        verdict = checker.check(bundle, make_query())
        assert verdict.status is SufficiencyStatus.INSUFFICIENT
        assert "threshold" in verdict.reason

    def test_some_above_threshold_sufficient(self) -> None:
        checker = SufficiencyChecker(min_score_threshold=0.5)
        items = [
            make_evidence(anchors={"rerank_score": 0.1}),
            make_evidence(anchors={"rerank_score": 0.8}),
        ]
        bundle = make_bundle(items)
        verdict = checker.check(bundle, make_query())
        assert verdict.status is SufficiencyStatus.SUFFICIENT

    def test_no_scores_treated_as_sufficient(self) -> None:
        """Missing score metadata should not block — treat as sufficient."""
        checker = SufficiencyChecker(min_score_threshold=0.5)
        items = [make_evidence(anchors={})]
        bundle = make_bundle(items)
        verdict = checker.check(bundle, make_query())
        assert verdict.status is SufficiencyStatus.SUFFICIENT

    def test_normal_evidence_sufficient(self) -> None:
        checker = SufficiencyChecker()
        items = [make_evidence(anchors={"rerank_score": 0.8})]
        bundle = make_bundle(items)
        verdict = checker.check(bundle, make_query())
        assert verdict.status is SufficiencyStatus.SUFFICIENT

    def test_threshold_edge_case(self) -> None:
        checker = SufficiencyChecker(min_score_threshold=0.5)
        items = [make_evidence(anchors={"rerank_score": 0.5})]
        bundle = make_bundle(items)
        verdict = checker.check(bundle, make_query())
        # Score == threshold means NOT below threshold → SUFFICIENT
        assert verdict.status is SufficiencyStatus.SUFFICIENT


# ===========================================================================
# P10-17: Bounded retry
# ===========================================================================


class TestBoundedRetry:
    """P10-17 bounded retrieval retry."""

    def test_sufficient_returns_none(self) -> None:
        verdict = SufficiencyVerdict(status=SufficiencyStatus.SUFFICIENT)
        result = apply_retry_strategy(make_query(), verdict, attempt=1)
        assert result is None

    def test_max_attempts_returns_none(self) -> None:
        verdict = SufficiencyVerdict(status=SufficiencyStatus.INSUFFICIENT, reason="empty")
        result = apply_retry_strategy(make_query(), verdict, attempt=2)
        assert result is None

    def test_insufficient_triggers_retry(self) -> None:
        verdict = SufficiencyVerdict(
            status=SufficiencyStatus.INSUFFICIENT,
            reason="too few",
            retry_hint="increase k",
        )
        query = make_query(top_k_dense=20, top_k_sparse=20)
        result = apply_retry_strategy(query, verdict, attempt=1)
        assert result is not None
        assert result.top_k_dense == 30
        assert result.top_k_sparse == 30

    def test_partial_triggers_retry(self) -> None:
        verdict = SufficiencyVerdict(
            status=SufficiencyStatus.PARTIAL,
            missing_document_ids=["d2"],
        )
        result = apply_retry_strategy(make_query(), verdict, attempt=1)
        assert result is not None

    def test_change_expansion_strategy(self) -> None:
        verdict = SufficiencyVerdict(status=SufficiencyStatus.INSUFFICIENT, reason="low")
        query = make_query(expansion_policy=ExpansionPolicy.NONE)
        result = apply_retry_strategy(query, verdict, attempt=1)
        assert result is not None
        assert result.expansion_policy is ExpansionPolicy.PARENT

    def test_max_attempts_hard_bound(self) -> None:
        """Default max is 2 — attempt=2 means we've used both."""
        assert DEFAULT_MAX_ATTEMPTS == 2
        verdict = SufficiencyVerdict(status=SufficiencyStatus.INSUFFICIENT, reason="bad")
        assert apply_retry_strategy(make_query(), verdict, attempt=2) is None

    def test_custom_policy_max(self) -> None:
        policy = RetryPolicy(max_attempts=3)
        verdict = SufficiencyVerdict(status=SufficiencyStatus.INSUFFICIENT, reason="bad")
        # attempt=2 is not exhausted when max=3
        result = apply_retry_strategy(make_query(), verdict, attempt=2, policy=policy)
        assert result is not None

    def test_relax_filter_strategy(self) -> None:
        verdict = SufficiencyVerdict(status=SufficiencyStatus.INSUFFICIENT, reason="filtered")
        policy = RetryPolicy(strategies=[RetryStrategy.RELAX_FILTER])
        query = make_query(source_filters={"type": "pdf"})
        result = apply_retry_strategy(query, verdict, attempt=1, policy=policy)
        assert result is not None
        assert result.source_filters == {}

    def test_no_applicable_strategy_returns_none(self) -> None:
        """When strategies can't improve anything, don't retry."""
        verdict = SufficiencyVerdict(status=SufficiencyStatus.INSUFFICIENT, reason="bad")
        policy = RetryPolicy(strategies=[RetryStrategy.RELAX_FILTER])
        query = make_query(source_filters={})  # already empty — nothing to relax
        result = apply_retry_strategy(query, verdict, attempt=1, policy=policy)
        assert result is None
