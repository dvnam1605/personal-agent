"""Deterministic grounding: identifier abstention and uncited data sentences."""

from __future__ import annotations

import uuid

from app.domain.models.retrieval import Evidence, EvidenceBundle
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.services.retrieval.grounding import abstain_message, check_answer, check_identifiers
from app.services.retrieval.synthesis import PromptAnswerSynthesizer


def _ev(content: str, *, number: str | None = None, title: str | None = None) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4().hex,
        kind="CHILD",
        content_raw=content,
        token_estimate=10,
        primary_chunk_id=uuid.uuid4().hex[:8],
        document_id="doc-1",
        anchors={"doc_number": number} if number else {},
        title=title,
    )


def _bundle(*items: Evidence) -> EvidenceBundle:
    return EvidenceBundle(
        items=list(items),
        total_tokens=10 * len(items),
        documents_used=["doc-1"],
        parent_ids_used=[],
        retrieval_trace_id=uuid.uuid4().hex,
    )


def test_abstains_when_named_number_is_in_no_evidence() -> None:
    bundle = _bundle(_ev("Quyết định về khen thưởng", number="90/QĐ-TNVN", title="QĐ 90"))
    verdict = check_identifiers("Quyết định 80/QĐ-TNVN quy định gì?", bundle)
    assert verdict.abstain is True
    message = abstain_message(verdict, bundle)
    assert "80/QĐ-TNVN" in message and "QĐ 90" in message


def test_matches_number_in_anchor_or_text_ignoring_case_and_spaces() -> None:
    bundle = _bundle(_ev("Căn cứ ...", number="80/QĐ-TNVN"))
    assert check_identifiers("quyết định 80 / qđ-tnvn nói gì", bundle).abstain is False
    bundle2 = _bundle(_ev("Theo hợp đồng số 45/2026/HĐDV giữa hai bên"))
    assert (
        check_identifiers("Hợp đồng 45/2026/HĐDV có giá trị bao nhiêu?", bundle2).abstain is False
    )


def test_no_identifier_never_abstains() -> None:
    assert check_identifiers("Quy chế chi tiêu nói gì?", _bundle(_ev("x"))).abstain is False


def test_uncited_data_sentences_are_reported() -> None:
    answer = (
        "### Tổng quan\n"
        "Định mức chi là 200.000 đồng/người [1]. Tổng kinh phí là 1.500.000.000 đồng.\n"
        "1. Mục thứ nhất [2]\n"
        "Tỷ lệ hoàn thành đạt 95%."
    )
    report = check_answer(answer)
    assert report.unsupported == [
        "Tổng kinh phí là 1.500.000.000 đồng.",
        "Tỷ lệ hoàn thành đạt 95%.",
    ]
    assert check_answer("Mọi thứ đều có dẫn chứng 2026 [1].").ok


async def test_uncited_figure_caps_status_at_partial() -> None:
    bundle = _bundle(_ev("Định mức 200.000 đồng"))

    async def gen(system: str, user: str) -> str:
        return "Định mức là 200.000 đồng [E1]. Tổng là 3.000.000 đồng."

    result = await PromptAnswerSynthesizer(generate=gen).synthesize("Q?", bundle)
    assert result.status is SufficiencyStatus.PARTIAL
    assert result.unsupported_claims == ["Tổng là 3.000.000 đồng."]


def test_number_spelling_variants_match() -> None:
    bundle = _bundle(_ev("Số: 80-QĐ/TNVN Hà Nội, ngày ..."))
    assert check_identifiers("Quyết định 80/QĐ-TNVN quy định gì?", bundle).abstain is False
