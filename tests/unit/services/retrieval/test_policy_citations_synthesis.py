"""Retrieval policy tests: citation extraction and answer synthesis (P10-18/19), the evidence contract (P10-15) and the prompt templates."""

from __future__ import annotations

import pytest

from app.domain.models.retrieval import (
    Evidence,
)
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.services.retrieval.answer.synthesis import (
    PromptAnswerSynthesizer,
    build_citations_from_bundle,
    extract_cited_ids,
)
from tests.unit.services.retrieval._policy_factories import (
    make_bundle,
    make_evidence,
)

# ===========================================================================
# P10-18/19: Answer synthesis
# ===========================================================================


class TestCitationExtraction:
    """P10-19 citation extraction from synthesis output."""

    def test_extract_cited_ids(self) -> None:
        eid = "ab" * 16  # exactly 32 hex chars
        text = f"According to [{eid}] the answer is 42."
        ids = extract_cited_ids(text)
        assert ids == [eid]

    def test_extract_multiple_unique(self) -> None:
        eid1 = "a" * 32
        eid2 = "b" * 32
        text = f"[{eid1}] and [{eid2}] and [{eid1}] again"
        ids = extract_cited_ids(text)
        assert ids == [eid1, eid2]  # unique, order preserved

    def test_extract_no_citations(self) -> None:
        ids = extract_cited_ids("No citations here")
        assert ids == []

    def test_extract_comma_separated_citations(self) -> None:
        eid1 = "a" * 32
        eid2 = "b" * 32
        text = f"Quy chế chi tiêu nội bộ [{eid1}, {eid2}]."
        ids = extract_cited_ids(text)
        assert ids == [eid1, eid2]

    def test_extract_semicolon_separated_citations(self) -> None:
        eid1 = "a" * 32
        eid2 = "b" * 32
        text = f"Quy chế chi tiêu nội bộ [{eid1}; {eid2}]."
        ids = extract_cited_ids(text)
        assert ids == [eid1, eid2]

    def test_build_citations_from_bundle(self) -> None:
        eid = "a" * 32
        item = make_evidence(
            eid=eid,
            doc="doc-1",
            title="Test Doc",
            heading_path=["Chapter 1", "Section 2"],
            source_type="pdf",
        )
        bundle = make_bundle([item])
        citations = build_citations_from_bundle([eid], bundle)
        assert len(citations) == 1
        c = citations[0]
        assert c.evidence_id == eid
        assert c.document_id == "doc-1"
        assert c.title == "Test Doc"
        assert c.heading_path == ["Chapter 1", "Section 2"]
        assert c.source_type == "pdf"

    def test_build_citations_deduplicates(self) -> None:
        eid = "a" * 32
        item = make_evidence(eid=eid)
        bundle = make_bundle([item])
        citations = build_citations_from_bundle([eid, eid], bundle)
        assert len(citations) == 1

    def test_build_citations_missing_id_dropped(self) -> None:
        bundle = make_bundle([])
        citations = build_citations_from_bundle(["non-existent-id"], bundle)
        assert citations == []


class TestAnswerSynthesis:
    """P10-20 answer synthesiser contract."""

    @pytest.mark.asyncio
    async def test_empty_bundle_no_answer(self) -> None:
        synth = PromptAnswerSynthesizer()
        bundle = make_bundle([])
        result = await synth.synthesize("what is X?", bundle)
        assert result.status is SufficiencyStatus.INSUFFICIENT
        assert result.citations == []

    @pytest.mark.asyncio
    async def test_custom_generate_callback(self) -> None:
        eid = "c" * 32
        item = make_evidence(eid=eid, content="answer is 42")
        bundle = make_bundle([item])

        async def fake_generate(system: str, user: str) -> str:
            return f"The answer is 42 [{eid}]"

        synth = PromptAnswerSynthesizer(generate=fake_generate)
        result = await synth.synthesize("what is X?", bundle)
        assert result.status is SufficiencyStatus.SUFFICIENT
        assert "42" in result.answer
        assert len(result.citations) == 1
        assert result.citations[0].evidence_id == eid

    @pytest.mark.asyncio
    async def test_multi_evidence_citations_synthesize(self) -> None:
        eid1 = "c" * 32
        eid2 = "d" * 32
        item1 = make_evidence(eid=eid1, content="answer part 1")
        item2 = make_evidence(eid=eid2, content="answer part 2")
        bundle = make_bundle([item1, item2])

        async def fake_generate(system: str, user: str) -> str:
            return f"Quy chế chi tiêu [{eid1}, {eid2}]."

        synth = PromptAnswerSynthesizer(generate=fake_generate)
        result = await synth.synthesize("Quy chế?", bundle)
        assert result.status is SufficiencyStatus.SUFFICIENT
        assert "[1][2]" in result.answer
        assert eid1 not in result.answer
        assert eid2 not in result.answer
        assert len(result.citations) == 2

    @pytest.mark.asyncio
    async def test_multi_evidence_citations_stream(self) -> None:
        eid1 = "c" * 32
        eid2 = "d" * 32
        item1 = make_evidence(eid=eid1, content="answer part 1")
        item2 = make_evidence(eid=eid2, content="answer part 2")
        bundle = make_bundle([item1, item2])

        async def fake_stream(system: str, user: str):
            yield "Quy chế chi tiêu ["
            yield f"{eid1}, "
            yield f"{eid2}]."

        synth = PromptAnswerSynthesizer(stream_generate=fake_stream)
        tokens = []
        citations = []
        async for event in synth.synthesize_stream("Quy chế?", bundle):
            if event["type"] == "token":
                tokens.append(event["delta"])
            elif event["type"] == "citations":
                citations = event.get("citations", [])
        full_text = "".join(tokens)
        assert "[1][2]" in full_text
        assert eid1 not in full_text
        assert eid2 not in full_text
        assert len(citations) == 2


# ===========================================================================
# P10-15: Evidence contract
# ===========================================================================


class TestEvidenceContract:
    """P10-15 evidence provenance fields."""

    def test_evidence_has_stable_id(self) -> None:
        e1 = make_evidence()
        e2 = make_evidence()
        assert len(e1.evidence_id) == 32
        assert e1.evidence_id != e2.evidence_id

    def test_evidence_preserves_provenance(self) -> None:
        e = make_evidence(
            doc="doc-1",
            title="My Report",
            source_type="pdf",
            heading_path=["Intro", "Background"],
        )
        assert e.document_id == "doc-1"
        assert e.title == "My Report"
        assert e.source_type == "pdf"
        assert e.heading_path == ["Intro", "Background"]

    def test_evidence_id_default_factory(self) -> None:
        """Each Evidence gets a unique id even without explicit assignment."""

        e = Evidence(
            kind="CHILD",
            content_raw="text",
            token_estimate=5,
            primary_chunk_id="c1",
            document_id="d1",
        )
        assert len(e.evidence_id) == 32


# ===========================================================================
# Prompt template tests
# ===========================================================================


class TestPromptTemplates:
    """Verify prompt structure for synthesis."""

    def test_system_prompt_contains_boundary(self) -> None:
        from app.services.retrieval.answer.prompts import SYNTHESIS_SYSTEM_PROMPT

        assert "UNTRUSTED" in SYNTHESIS_SYSTEM_PROMPT
        assert "evidence" in SYNTHESIS_SYSTEM_PROMPT.lower()

    def test_user_message_with_evidence(self) -> None:
        from app.services.retrieval.answer.prompts import build_synthesis_user_message

        items = [make_evidence(eid="e" * 32, content="test content")]
        bundle = make_bundle(items)
        msg = build_synthesis_user_message("What is X?", bundle)
        assert "What is X?" in msg
        assert "<retrieved_document" in msg
        assert "test content" in msg

    def test_user_message_empty_bundle(self) -> None:
        from app.services.retrieval.answer.prompts import build_synthesis_user_message

        bundle = make_bundle([])
        msg = build_synthesis_user_message("What is X?", bundle)
        assert "No evidence" in msg

    def test_user_message_with_missing_documents_warning(self) -> None:
        """L3: User message includes coverage warning when missing documents are passed."""
        from app.services.retrieval.answer.prompts import build_synthesis_user_message

        items = [make_evidence(eid="e" * 32, doc="d1")]
        bundle = make_bundle(items)
        msg = build_synthesis_user_message("What is X?", bundle, missing_documents=["d2"])
        assert "Coverage Warning" in msg
        assert "d2" in msg
