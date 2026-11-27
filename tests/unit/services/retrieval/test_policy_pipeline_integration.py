"""Retrieval policy tests: sufficiency, retry and synthesis wired through the pipeline (P10C), plus provenance and expansion contracts."""

from __future__ import annotations

import uuid

import pytest

from app.domain.models.retrieval import (
    EvidenceBundle,
    ExpansionPolicy,
    RetrievalMode,
    RetrievedChunk,
)
from app.domain.models.retrieval.sufficiency import SufficiencyStatus, SufficiencyVerdict
from app.services.retrieval.answer.synthesis import (
    PromptAnswerSynthesizer,
    extract_cited_ids,
)
from app.services.retrieval.evidence.retry import (
    RetryPolicy,
    apply_retry_strategy,
)
from app.services.retrieval.evidence.sufficiency import SufficiencyChecker
from app.services.retrieval.pipeline import RetrievalPipeline
from tests.unit.services.retrieval._policy_factories import (
    FakeProvider,
    FixedHybrid,
    chunk,
    make_bundle,
    make_evidence,
    make_query,
)

# ===========================================================================
# P10C pipeline integration
# ===========================================================================


class TestPipelineIntegration:
    """P10C pipeline: sufficiency + retry + synthesis integration."""

    @pytest.mark.asyncio
    async def test_sufficient_no_retry(self) -> None:
        """Good results → SUFFICIENT, no retry round."""
        chunks = [chunk("c1", 0.9), chunk("c2", 0.8)]
        hybrid = FixedHybrid(chunks)
        pipe = RetrievalPipeline(hybrid, FakeProvider())
        bundle, verdict, compare = await pipe.run_with_sufficiency(make_query())
        assert verdict.status is SufficiencyStatus.SUFFICIENT
        assert compare is None
        assert hybrid.call_count == 1  # no retry

    @pytest.mark.asyncio
    async def test_empty_triggers_retry(self) -> None:
        """Empty results → INSUFFICIENT → retry (but still empty)."""
        hybrid = FixedHybrid([])
        pipe = RetrievalPipeline(hybrid, FakeProvider())
        bundle, verdict, _ = await pipe.run_with_sufficiency(make_query())
        assert verdict.status is SufficiencyStatus.INSUFFICIENT
        assert hybrid.call_count == 2  # initial + 1 retry

    @pytest.mark.asyncio
    async def test_retry_max_attempts_bounded(self) -> None:
        """Hard bound: max 2 attempts even when INSUFFICIENT persists."""
        hybrid = FixedHybrid([])
        policy = RetryPolicy(max_attempts=2)
        pipe = RetrievalPipeline(hybrid, FakeProvider(), retry_policy=policy)
        bundle, verdict, _ = await pipe.run_with_sufficiency(make_query())
        assert hybrid.call_count == 2  # exactly 2 = initial + 1 retry

    @pytest.mark.asyncio
    async def test_compare_mode_enforces_diversity(self) -> None:
        """COMPARE_DOCUMENTS mode triggers compare result."""
        chunks = [chunk("c1", 0.9, doc="d1"), chunk("c2", 0.8, doc="d2")]
        hybrid = FixedHybrid(chunks)
        query = make_query(
            mode=RetrievalMode.COMPARE_DOCUMENTS,
            document_ids=["d1", "d2"],
        )
        pipe = RetrievalPipeline(hybrid, FakeProvider())
        bundle, verdict, compare = await pipe.run_with_sufficiency(query)
        assert compare is not None
        assert compare.covered_documents == ["d1", "d2"]

    @pytest.mark.asyncio
    async def test_run_preserves_existing_api(self) -> None:
        """Existing run() method still returns EvidenceBundle directly."""
        chunks = [chunk("c1", 0.9)]
        hybrid = FixedHybrid(chunks)
        pipe = RetrievalPipeline(hybrid, FakeProvider())
        bundle = await pipe.run(make_query())
        assert isinstance(bundle, EvidenceBundle)
        assert len(bundle.items) == 1

    @pytest.mark.asyncio
    async def test_synthesis_insufficient_no_answer(self) -> None:
        """Empty retrieval → synthesis returns explicit no-answer."""
        hybrid = FixedHybrid([])
        pipe = RetrievalPipeline(hybrid, FakeProvider())
        result = await pipe.run_with_synthesis(make_query())
        assert result.status is SufficiencyStatus.INSUFFICIENT
        assert result.citations == []

    @pytest.mark.asyncio
    async def test_synthesis_with_generate(self) -> None:
        """Full synthesis path with a fake LLM generate callback."""
        chunks = [chunk("c1", 0.9)]
        hybrid = FixedHybrid(chunks)

        async def fake_gen(system: str, user: str) -> str:
            return "Answer based on evidence [E1]."

        synth = PromptAnswerSynthesizer(generate=fake_gen)
        pipe = RetrievalPipeline(hybrid, FakeProvider(), synthesizer=synth)
        result = await pipe.run_with_synthesis(make_query())
        assert result.status is SufficiencyStatus.SUFFICIENT
        assert "Answer" in result.answer

    @pytest.mark.asyncio
    async def test_synthesis_preserves_partial_status(self) -> None:
        """H4: PARTIAL status preserved through synthesis when compare docs are missing."""
        pid = uuid.uuid4().hex
        doc1 = uuid.uuid4().hex
        doc2 = uuid.uuid4().hex
        chunks = [chunk("c1", 0.9, doc=doc1, parent=pid)]
        provider = FakeProvider(
            rows=[
                {
                    "chunk_id": pid,
                    "document_id": doc1,
                    "content_raw": "Parent text for doc1",
                    "heading_path": ["Intro"],
                    "document_title": "Doc 1 Title",
                    "source_type": "pdf",
                    "uri": "doc1.pdf",
                    "version_number": 1,
                }
            ]
        )
        hybrid = FixedHybrid(chunks)
        query = make_query(
            mode=RetrievalMode.COMPARE_DOCUMENTS,
            document_ids=[doc1, doc2],
        )

        captured_user_msg = []

        async def fake_gen(system: str, user: str) -> str:
            captured_user_msg.append(user)
            return "Partial answer comparing available data."

        synth = PromptAnswerSynthesizer(generate=fake_gen)
        pipe = RetrievalPipeline(hybrid, provider, synthesizer=synth)
        result = await pipe.run_with_synthesis(query)
        assert result.status is SufficiencyStatus.PARTIAL
        assert doc2 in captured_user_msg[0]
        assert "Coverage Warning" in captured_user_msg[0]

    @pytest.mark.asyncio
    async def test_synthesis_with_external_knowledge(self) -> None:
        """H2: internal_only=False allows synthesis with external knowledge and does not crash."""
        chunks = [chunk("c1", 0.9)]
        hybrid = FixedHybrid(chunks)
        captured = {}

        async def fake_gen(system: str, user: str) -> str:
            captured["system"] = system
            captured["user"] = user
            import re

            m = re.search(r'id="(E\d+)"', user)
            eid = m.group(1) if m else "E1"
            return f"Answer synthesizing internal and external knowledge [{eid}]."

        synth = PromptAnswerSynthesizer(generate=fake_gen)
        pipe = RetrievalPipeline(hybrid, FakeProvider(), synthesizer=synth)
        result = await pipe.run_with_synthesis(make_query(), internal_only=False)
        assert result.status is SufficiencyStatus.SUFFICIENT
        assert "supplemented by general external knowledge" in captured["system"]
        assert len(result.citations) == 1

    @pytest.mark.asyncio
    async def test_synthesis_factory_wires_generate_callback(self) -> None:
        """H2: build_retrieval_pipeline properly wires generate callback to synthesizer."""
        from app.services.retrieval.factory import build_retrieval_pipeline

        async def fake_gen(system: str, user: str) -> str:
            return "Generated answer [c1]"

        pipe = build_retrieval_pipeline(
            provider=FakeProvider(),
            hybrid_service=FixedHybrid([]),
            use_viranker=False,
            generate=fake_gen,
        )
        assert isinstance(pipe._synthesizer, PromptAnswerSynthesizer)
        assert pipe._synthesizer._generate is fake_gen

    @pytest.mark.asyncio
    async def test_low_score_triggers_insufficient_in_pipeline(self) -> None:
        """H1 / H-NEW: Configured score threshold (e.g. 0.15) evaluates low-score chunks (<0.15) to INSUFFICIENT."""
        chunks = [chunk("c1", 0.05, doc="d1")]
        hybrid = FixedHybrid(chunks)
        checker = SufficiencyChecker(min_score_threshold=0.15)
        pipe = RetrievalPipeline(hybrid, FakeProvider(), sufficiency_checker=checker)
        bundle, verdict, _ = await pipe.run_with_sufficiency(make_query())
        assert verdict.status is SufficiencyStatus.INSUFFICIENT
        assert hybrid.call_count == 2  # retry was triggered

    @pytest.mark.asyncio
    async def test_default_v1_threshold_allows_rrf_score(self) -> None:
        """H-NEW: Default V1 threshold (0.0) allows RRF fusion scores (<=0.033) without false INSUFFICIENT."""
        chunks = [chunk("c1", 0.025, doc="d1")]
        hybrid = FixedHybrid(chunks)
        pipe = RetrievalPipeline(hybrid, FakeProvider())  # default checker (min_score=0.0)
        bundle, verdict, _ = await pipe.run_with_sufficiency(make_query())
        assert verdict.status is SufficiencyStatus.SUFFICIENT
        assert hybrid.call_count == 1  # no unnecessary retry

    @pytest.mark.asyncio
    async def test_retry_distinct_trace_id_per_attempt(self) -> None:
        """M3: Retry attempts produce distinct trace IDs."""
        hybrid = FixedHybrid([])
        pipe = RetrievalPipeline(hybrid, FakeProvider())
        bundle, verdict, _ = await pipe.run_with_sufficiency(make_query())
        assert "-att2" in bundle.retrieval_trace_id

    @pytest.mark.asyncio
    async def test_internal_only_false_synthesizes_successfully(self) -> None:
        """H2: internal_only=False does not raise NotImplementedError and synthesizes successfully."""

        async def fake_gen(sys: str, usr: str) -> str:
            return "Generated answer [E1]"

        synth = PromptAnswerSynthesizer(generate=fake_gen)
        bundle = make_bundle([make_evidence()])
        res = await synth.synthesize("question", bundle, internal_only=False)
        assert res.answer == "Generated answer [1]"
        assert res.status is SufficiencyStatus.SUFFICIENT


# ===========================================================================
# Extra contract & policy tests
# ===========================================================================


class TestExtraPolicies:
    def test_change_expansion_from_neighbors(self) -> None:
        """M2: CHANGE_EXPANSION switches NEIGHBORS to PARENT."""
        verdict = SufficiencyVerdict(status=SufficiencyStatus.INSUFFICIENT, reason="low")
        query = make_query(expansion_policy=ExpansionPolicy.NEIGHBORS)
        result = apply_retry_strategy(query, verdict, attempt=1)
        assert result is not None
        assert result.expansion_policy is ExpansionPolicy.PARENT

    def test_extract_cited_ids_hallucinated(self) -> None:
        """L2: Hallucinated or non-hex markers are safely ignored."""
        text = "According to [Doc-1 p.12] and [xyz] and [12345] nothing matches."
        ids = extract_cited_ids(text)
        assert ids == []

    def test_unit_for_chunk_populates_provenance(self) -> None:
        """H1 & H2: unit_for_chunk sets score, rerank_score, and provenance fields."""
        from app.services.retrieval.evidence.packing import unit_for_chunk

        c = RetrievedChunk(
            chunk_id="c-1",
            parent_id="p-1",
            document_id="d-1",
            content_raw="chunk content",
            score=0.88,
            rerank_score=0.92,
            retrieval_type="hybrid",
            metadata={
                "document_title": "Annual Report 2026",
                "filename": "annual-report.pdf",
                "uri": "drive://annual.pdf",
                "source_type": "drive_file",
                "version_number": 2,
            },
        )
        ev = unit_for_chunk(c)
        assert ev.score == 0.88
        assert ev.rerank_score == 0.92
        assert ev.title == "Annual Report 2026"
        assert ev.filename == "annual-report.pdf"
        assert ev.source_type == "drive_file"
        assert ev.document_version_id == "d-1"
        assert ev.anchors.get("version_number") == 2

    @pytest.mark.asyncio
    async def test_parent_expansion_populates_provenance(self) -> None:
        """M1 & H2: _parent_units populates score, anchors from parent row, and provenance."""
        from app.services.retrieval.search.expansion import ExpansionService

        pid = uuid.uuid4().hex
        did = uuid.uuid4().hex
        version_id = uuid.uuid4().hex
        cid = uuid.uuid4().hex

        fake_provider = FakeProvider(
            rows=[
                {
                    "chunk_id": pid,
                    "document_id": did,
                    "content_raw": "Parent full text",
                    "heading_path": ["Chapter 1"],
                    "page_start": 5,
                    "page_end": 7,
                    "citation_label": "p. 5-7",
                    "document_title": "Doc Title",
                    "source_type": "pdf",
                    "uri": "drive://annual.pdf",
                    "filename": "annual-report.pdf",
                    "document_version_id": version_id,
                    "version_number": 1,
                }
            ]
        )
        service = ExpansionService(fake_provider)
        c = RetrievedChunk(
            chunk_id=cid,
            parent_id=pid,
            document_id=did,
            content_raw="Child text",
            score=0.85,
            rerank_score=0.90,
            retrieval_type="hybrid",
            metadata={"document_title": "Doc Title", "source_type": "pdf"},
        )
        query = make_query(expansion_policy=ExpansionPolicy.PARENT)

        evidences = await service.build_units([c], ExpansionPolicy.PARENT, query)
        assert len(evidences) == 1
        ev = evidences[0]
        assert ev.kind == "PARENT"
        assert ev.score == 0.85
        assert ev.rerank_score == 0.90
        assert ev.title == "Doc Title"
        assert ev.filename == "annual-report.pdf"
        assert ev.source_type == "pdf"
        assert ev.document_version_id == version_id
        assert ev.document_version_id != did
        assert ev.anchors.get("page_start") == 5
        assert ev.anchors.get("page_end") == 7
        assert ev.anchors.get("version_number") == 1

    def test_provenance_fallback_to_title_when_uri_none(self) -> None:
        """H9: When uri is None (local corpus), filename falls back to document_title."""
        from app.services.retrieval.evidence.packing import unit_for_chunk

        c = RetrievedChunk(
            chunk_id="c-local-1",
            parent_id="p-local-1",
            document_id="doc-uuid-12345",
            content_raw="van ban noi bo",
            score=0.9,
            retrieval_type="hybrid",
            metadata={
                "document_title": "quyet_dinh_123.pdf",
                "uri": None,
                "source_type": "local_file",
                "version_number": 1,
            },
        )
        ev = unit_for_chunk(c)
        assert ev.document_version_id == "doc-uuid-12345"
        assert ev.filename == "quyet_dinh_123.pdf"
        assert ev.title == "quyet_dinh_123.pdf"
