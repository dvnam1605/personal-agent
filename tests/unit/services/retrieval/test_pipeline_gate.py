"""The pre-synthesis gate shared by the plain and streaming pipeline paths."""

from __future__ import annotations

import uuid

from app.domain.models.retrieval import Evidence, EvidenceBundle, RetrievalMode, RetrievalQuery
from app.domain.models.retrieval.sufficiency import SufficiencyStatus, SufficiencyVerdict
from app.services.retrieval.evidence.compare_policy import CompareResult
from app.services.retrieval.pipeline import _gate


def _bundle(*contents: str) -> EvidenceBundle:
    items = [
        Evidence(
            evidence_id=uuid.uuid4().hex,
            kind="CHILD",
            content_raw=c,
            token_estimate=5,
            primary_chunk_id=uuid.uuid4().hex[:8],
            document_id="doc-1",
        )
        for c in contents
    ]
    return EvidenceBundle(
        items=items,
        total_tokens=5 * len(items),
        documents_used=["doc-1"] if items else [],
        parent_ids_used=[],
        retrieval_trace_id="t",
    )


def _query(text: str = "Quy chế chi tiêu?", **kw: object) -> RetrievalQuery:
    return RetrievalQuery(original_query=text, search_query=text, **kw)  # type: ignore[arg-type]


_OK = SufficiencyVerdict(status=SufficiencyStatus.SUFFICIENT)


def test_empty_bundle_refuses_with_the_audience_message() -> None:
    internal = _gate(_query(), _bundle(), _OK, None, internal_only=True)
    external = _gate(_query(), _bundle(), _OK, None, internal_only=False)
    assert internal.refusal and "nội bộ" in internal.refusal
    assert external.refusal and "nội bộ" not in external.refusal


def test_insufficient_refuses_only_internal_answers() -> None:
    verdict = SufficiencyVerdict(status=SufficiencyStatus.INSUFFICIENT)
    assert _gate(_query(), _bundle("x"), verdict, None, internal_only=True).refusal
    assert _gate(_query(), _bundle("x"), verdict, None, internal_only=False).refusal is None


def test_missing_documents_only_for_compare_mode() -> None:
    verdict = SufficiencyVerdict(status=SufficiencyStatus.PARTIAL, missing_document_ids=["d2"])
    compare = CompareResult(missing_documents=["d3"])
    plain = _gate(_query(), _bundle("x"), verdict, None, internal_only=True)
    compared = _gate(
        _query(mode=RetrievalMode.COMPARE_DOCUMENTS, document_ids=["d1", "d2", "d3"]),
        _bundle("x"),
        verdict,
        compare,
        internal_only=True,
    )
    assert plain.refusal is None and plain.missing_documents is None
    assert compared.missing_documents == ["d2", "d3"]
