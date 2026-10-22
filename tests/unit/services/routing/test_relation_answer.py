"""Relation questions: SQL picks documents + facts, their text is read, an LLM answers."""

from __future__ import annotations

from typing import Any

from app.domain.models.retrieval.citation import Citation
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.services.retrieval.answer.synthesis import SynthesisResult
from app.services.retrieval.answer.verbalize import accept_phrasing
from app.services.retrieval.relations.service import RelationAnswer
from app.services.routing.query.orchestrator import QueryOrchestrator

DRAFT = (
    "Trong kho có 1 văn bản:\n"
    "1. Quyết định số 627/QĐ-TNVN ngày 18/03/2026 về việc bổ nhiệm lại cán bộ [1]: "
    "bổ nhiệm Đinh Thị Thu Trang giữ chức Trưởng phòng Phòng Y tế."
)


def _relation(total: int = 1, targets: tuple[str, ...] = ("Đinh Thị Thu Trang",)) -> RelationAnswer:
    return RelationAnswer(
        answer=DRAFT,
        citations=[
            Citation(evidence_id=f"doc:d{i}", document_id=f"d{i}", citation_type="document")
            for i in range(total)
        ],
        total=total,
        trace={},
        facts=[
            {"document_id": "d0", "relation_type": "appoints", "target_text": t, "attributes": {}}
            for t in targets
        ],
    )


class FakeRelations:
    def __init__(self, answer: RelationAnswer | None) -> None:
        self._answer = answer

    async def answer(self, query: str, requester: Any) -> RelationAnswer | None:
        return self._answer


class FakePipeline:
    def __init__(self, result: SynthesisResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[Any, str | None]] = []

    async def run_with_synthesis(self, query: Any, **kwargs: Any) -> SynthesisResult:
        self.calls.append((query, kwargs.get("system_prompt")))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _orchestrator(relation: RelationAnswer, pipeline: FakePipeline) -> QueryOrchestrator:
    orch = QueryOrchestrator(relation_service=FakeRelations(relation), phrase_structured=False)
    orch._pipeline = pipeline
    return orch


def _chunk_citation() -> Citation:
    return Citation(evidence_id="e1", document_id="d0", chunk_id="c1", title="QĐ 627")


async def test_relation_documents_are_read_and_facts_go_to_the_prompt() -> None:
    answer = (
        "Có một quyết định bổ nhiệm: Quyết định số 627/QĐ-TNVN ngày 18/03/2026 bổ nhiệm lại "
        "bà Đinh Thị Thu Trang giữ chức Trưởng phòng Phòng Y tế, Ban VOV2, thời hạn 5 năm [1]."
    )
    pipeline = FakePipeline(SynthesisResult(answer=answer, citations=[_chunk_citation()]))
    result = await _orchestrator(_relation(), pipeline)._run_retrieval(
        "Trong các tài liệu có những quyết định bổ nhiệm nào?", "u1"
    )
    query, system_prompt = pipeline.calls[0]
    assert query.document_ids == ["d0"]  # scoped to the documents SQL selected
    assert "Quyết định số 627/QĐ-TNVN" in (system_prompt or "")  # facts in the prompt
    assert "627/QĐ-TNVN" not in query.original_query  # not in the question
    assert result.answer == answer and result.citations[0].chunk_id == "c1"
    assert result.status is SufficiencyStatus.SUFFICIENT


async def test_targets_the_llm_left_out_are_appended() -> None:
    pipeline = FakePipeline(
        SynthesisResult(answer="Quyết định 1119 khen Văn phòng [1].", citations=[_chunk_citation()])
    )
    relation = _relation(targets=("Văn phòng", "Ban Thư ký biên tập"))
    result = await _orchestrator(relation, pipeline)._run_retrieval("QĐ 1119 khen những ai?", "u1")
    assert result.answer.endswith("còn có: Ban Thư ký biên tập.")


async def test_pipeline_failure_falls_back_to_the_fact_list() -> None:
    pipeline = FakePipeline(RuntimeError("gateway down"))
    result = await _orchestrator(_relation(), pipeline)._run_retrieval("bổ nhiệm nào?", "u1")
    assert result.answer == DRAFT and result.citations[0].citation_type == "document"


async def test_too_many_documents_skip_reading() -> None:
    pipeline = FakePipeline(SynthesisResult(answer="x", citations=[]))
    result = await _orchestrator(_relation(total=20), pipeline)._run_retrieval(
        "căn cứ NĐ 152?", "u1"
    )
    assert pipeline.calls == [] and result.answer == DRAFT


def test_phrasing_must_keep_facts_and_citations() -> None:
    good = (
        "Trong kho có một quyết định bổ nhiệm: Quyết định số 627/QĐ-TNVN ngày 18/03/2026 "
        "bổ nhiệm lại bà Đinh Thị Thu Trang làm Trưởng phòng Phòng Y tế [1]."
    )
    assert accept_phrasing(DRAFT, good)
    assert not accept_phrasing(DRAFT, good.replace(" [1]", ""))  # citation dropped
    assert not accept_phrasing(DRAFT, good.replace("18/03/2026", "19/03/2026"))  # figure changed
    assert not accept_phrasing(DRAFT, good + " Xem thêm Quyết định 700/QĐ-TNVN.")  # new document
    assert not accept_phrasing(DRAFT, good + " theo quan hệ đã lưu")


async def test_selector_relations_are_not_required_in_a_who_signed_answer() -> None:
    relation = _relation()
    relation = RelationAnswer(
        answer=relation.answer,
        citations=relation.citations,
        total=1,
        trace={},
        facts=[
            {"document_id": "d0", "relation_type": "signed_by", "target_text": "Đỗ Tiến Sỹ"},
            {"document_id": "d0", "relation_type": "awards", "target_text": "Văn phòng"},
        ],
    )
    pipeline = FakePipeline(
        SynthesisResult(
            answer="Quyết định 1119 do Đỗ Tiến Sỹ ký [1].", citations=[_chunk_citation()]
        )
    )
    result = await _orchestrator(relation, pipeline)._run_retrieval("Ai ký QĐ khen thưởng?", "u1")
    assert result.answer == "Quyết định 1119 do Đỗ Tiến Sỹ ký [1]."  # "Văn phòng" not appended
