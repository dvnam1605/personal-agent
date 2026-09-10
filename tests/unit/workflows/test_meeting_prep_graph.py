"""Unit and integration tests for WF-05 Meeting Prep Graph (spec P19).

Covers:
- Graph compilation and structural invariants (P19 §3.2).
- Node 1: IdentifyMeeting event lookup and missing-meeting graceful exit.
- Node 2: ResolveContext attendee profile normalization and topic extraction.
- Node 3A & 3B: Parallel concurrent execution via LangGraph Send API.
- Node 4: Fan-in Reducer evidence aggregation and MeetingDossier schema validation.
- Least-privilege isolation against mutation tools.
- Full end-to-end execution through HarnessDispatcher.
- Benchmark and comparative validation against dynamic skill baseline (P19-04).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.enums import Domain, RouteType
from app.domain.errors import PermissionDeniedError
from app.domain.models import AssistantState, RouteDecision
from app.domain.models.supervisor.meeting_dossier import (
    MeetingAttendee,
    MeetingDocumentRef,
    MeetingDossier,
)
from app.harness.dispatch import HarnessDispatcher
from app.harness.workflow_channels import WorkflowState
from app.harness.workflows.meeting_prep import (
    FORBIDDEN_MUTATION_TOOLS,
    assert_read_only_tool,
    build_meeting_prep_graph,
)
from app.services.routing.workflow_registry import (
    load_default_workflow_registry,
)
from tests.evaluation.benchmark_meeting_prep import (
    run_meeting_prep_benchmark,
)


@pytest.fixture
def base_assistant_state() -> AssistantState:
    return AssistantState(
        user_id="user_p19_test",
        request="Chuẩn bị họp ngày mai với Nam",
        goal="Chuẩn bị tài liệu cuộc họp",
    )


@pytest.fixture
def sample_meeting_context() -> dict[str, Any]:
    return {
        "event_id": "evt_p19_001",
        "title": "Họp Chiến Lược AI Assistant 2026",
        "summary": "Đánh giá kiến trúc StateGraph và kế hoạch phát hành V1",
        "start_time": "2026-09-10T10:00:00Z",
        "attendees": [
            {"email": "nam@tech.vn", "name": "Nam Đỗ", "role": "Lead Architect"},
            "linh@tech.vn",
        ],
    }


class TestMeetingPrepGraphTopologyAndCompilation:
    def test_meeting_prep_graph_compilation(self) -> None:
        """Verify WF-05 compiles into a valid executable graph."""
        graph = build_meeting_prep_graph()
        assert graph is not None
        assert hasattr(graph, "ainvoke")
        assert hasattr(graph, "invoke")

    def test_least_privilege_no_mutations_allowed(self) -> None:
        """Verify mutation tools are strictly forbidden in WF-05 (least-privilege per M2)."""
        for tool in FORBIDDEN_MUTATION_TOOLS:
            with pytest.raises(PermissionDeniedError, match="forbidden in WF-05"):
                assert_read_only_tool(tool)

        # Read-only tools pass cleanly
        assert_read_only_tool("calendar.get_event")
        assert_read_only_tool("gmail.search_messages")
        assert_read_only_tool("retrieval.retrieve")
        assert_read_only_tool("drive.search_files")

    def test_allowed_tools_enforcement_at_graph_compilation(self) -> None:
        """M2 Fix: Supplying mutation tool in allowed_tools triggers PermissionDeniedError at build time."""
        with pytest.raises(PermissionDeniedError, match="forbidden in WF-05"):
            build_meeting_prep_graph(allowed_tools=["calendar.delete_event"])

        with pytest.raises(PermissionDeniedError, match="forbidden in WF-05"):
            build_meeting_prep_graph(allowed_tools=["gmail.send_draft"])

        with pytest.raises(PermissionDeniedError, match="forbidden in WF-05"):
            build_meeting_prep_graph(allowed_tools=["drive.update_permissions"])

    def test_runtime_default_builder_wires_cleanly(self) -> None:
        """Verify runtime.py default builder compiles with read-only enforcement."""
        from app.harness.runtime import build_default_meeting_prep_graph_builder

        graph = build_default_meeting_prep_graph_builder()
        assert graph is not None
        assert hasattr(graph, "ainvoke")


class TestNodeExecutionAndInvariants:
    @pytest.mark.asyncio
    async def test_identify_meeting_node_locates_target_event(
        self, sample_meeting_context: dict[str, Any]
    ) -> None:
        """Node 1 locates meeting via calendar finder adapter."""

        def custom_finder(ctx: dict[str, Any]) -> dict[str, Any]:
            assert "query" in ctx
            return sample_meeting_context

        graph = build_meeting_prep_graph(calendar_finder=custom_finder)
        state_in: WorkflowState = {
            "workflow_id": "WF-05",
            "run_id": "run_01",
            "user_id": "user_01",
            "query": "Họp Chiến Lược AI Assistant",
            "parameters": {},
        }

        res = await graph.ainvoke(state_in)
        assert res.get("status") in ("completed", "dossier_synthesized")
        dossier = res.get("dossier", {})
        assert dossier.get("meeting_id") == "evt_p19_001"
        assert dossier.get("event_summary") == "Họp Chiến Lược AI Assistant 2026"

    @pytest.mark.asyncio
    async def test_missing_meeting_graceful_exit(self) -> None:
        """Node 1 exits gracefully with no_meeting_found when no calendar event matches."""

        def empty_finder(ctx: dict[str, Any]) -> dict[str, Any] | None:
            return None

        graph = build_meeting_prep_graph(calendar_finder=empty_finder)
        state_in: WorkflowState = {
            "workflow_id": "WF-05",
            "run_id": "run_missing",
            "user_id": "user_01",
            "query": "Họp với người ngoài hành tinh",
            "parameters": {},
        }

        res = await graph.ainvoke(state_in)
        assert res.get("status") == "no_meeting_found"
        out = res.get("output", {})
        assert out.get("status") == "no_meeting_found"
        assert "Không tìm thấy" in out.get("message", "")

    @pytest.mark.asyncio
    async def test_bare_default_graph_emits_zero_fabricated_items(
        self, sample_meeting_context: dict[str, Any]
    ) -> None:
        """H2 Fix: Default bare graph with no adapters must emit ZERO fabricated email discussions or document citations."""
        graph = build_meeting_prep_graph()
        state_in: WorkflowState = {
            "workflow_id": "WF-05",
            "run_id": "run_no_fabrication",
            "user_id": "user_01",
            "query": "Chuẩn bị họp",
            "parameters": {"meeting_context": sample_meeting_context},
        }

        res = await graph.ainvoke(state_in)
        dossier = res.get("dossier", {})
        # Must NOT contain fake placeholder emails or fake citation IDs like doc_ref_auto
        assert dossier.get("recent_discussions") == []
        assert dossier.get("relevant_documents") == []
        for doc in dossier.get("relevant_documents", []):
            assert doc.get("citation_id") != "doc_ref_auto"

    @pytest.mark.asyncio
    async def test_resolve_context_extracts_attendees(
        self, sample_meeting_context: dict[str, Any]
    ) -> None:
        """Node 2 normalizes string and dict attendees and extracts agenda topics."""
        graph = build_meeting_prep_graph()
        state_in: WorkflowState = {
            "workflow_id": "WF-05",
            "run_id": "run_ctx",
            "user_id": "user_01",
            "query": "Chuẩn bị họp",
            "parameters": {"meeting_context": sample_meeting_context},
        }

        res = await graph.ainvoke(state_in)
        dossier = res.get("dossier", {})
        attendees = dossier.get("attendees", [])
        assert len(attendees) == 2
        emails = {a["email"] for a in attendees}
        assert "nam@tech.vn" in emails
        assert "linh@tech.vn" in emails

    @pytest.mark.asyncio
    async def test_parallel_email_and_doc_research(
        self, sample_meeting_context: dict[str, Any]
    ) -> None:
        """Nodes 3A and 3B execute concurrently with verified temporal overlap."""
        task_delay = 0.1
        start_times: dict[str, float] = {}
        end_times: dict[str, float] = {}

        def timed_email(ctx: dict[str, Any]) -> list[dict[str, Any]]:
            start_times["email"] = time.perf_counter()
            time.sleep(task_delay)
            end_times["email"] = time.perf_counter()
            return [{"from": "nam@tech.vn", "snippet": "Đã sẵn sàng demo LangGraph"}]

        def timed_doc(ctx: dict[str, Any]) -> list[dict[str, Any]]:
            start_times["doc"] = time.perf_counter()
            time.sleep(task_delay)
            end_times["doc"] = time.perf_counter()
            return [
                {"title": "ADR 0011", "citation_id": "doc_adr11", "content": "LangGraph Substrate"}
            ]

        graph = build_meeting_prep_graph(
            email_researcher=timed_email,
            doc_researcher=timed_doc,
        )

        state_in: WorkflowState = {
            "workflow_id": "WF-05",
            "run_id": "run_parallel",
            "user_id": "user_01",
            "query": "Chuẩn bị họp",
            "parameters": {"meeting_context": sample_meeting_context},
        }

        t0 = time.perf_counter()
        res = await graph.ainvoke(state_in)
        wall_elapsed = time.perf_counter() - t0
        assert res.get("status") in ("completed", "dossier_synthesized")

        # Verify temporal overlap (concurrency)
        assert "email" in start_times and "doc" in start_times
        # Total wall time is significantly less than sequential sum (2 * task_delay)
        assert wall_elapsed < (task_delay * 1.8), (
            f"Sequential execution detected: elapsed {wall_elapsed:.3f}s >= {task_delay * 1.8:.3f}s"
        )

    @pytest.mark.asyncio
    async def test_fan_in_reducer_merges_research_evidence(
        self, sample_meeting_context: dict[str, Any]
    ) -> None:
        """Fan-in Reducer cleanly combines email threads and document citations."""

        def custom_email(ctx: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {
                    "from": "nam@tech.vn",
                    "snippet": "Báo cáo tiến độ",
                    "commitments": ["Gửi tài liệu trước 9h"],
                }
            ]

        def custom_doc(ctx: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {
                    "title": "Kế hoạch V1",
                    "citation_id": "doc_v1_plan",
                    "domain": "rag",
                    "snippet": "Lộ trình ra mắt trợ lý",
                }
            ]

        graph = build_meeting_prep_graph(
            email_researcher=custom_email,
            doc_researcher=custom_doc,
        )

        state_in: WorkflowState = {
            "workflow_id": "WF-05",
            "run_id": "run_fan_in",
            "user_id": "user_01",
            "query": "Chuẩn bị họp",
            "parameters": {"meeting_context": sample_meeting_context},
        }

        res = await graph.ainvoke(state_in)
        dossier = res.get("dossier", {})
        discussions = dossier.get("recent_discussions", [])
        assert any("nam@tech.vn" in d for d in discussions)
        assert any("Gửi tài liệu trước 9h" in d for d in discussions)

        docs = dossier.get("relevant_documents", [])
        assert len(docs) >= 1
        assert docs[0]["citation_id"] == "doc_v1_plan"
        assert docs[0]["title"] == "Kế hoạch V1"

    @pytest.mark.asyncio
    async def test_synthesize_dossier_generates_schema(
        self, sample_meeting_context: dict[str, Any]
    ) -> None:
        """Verify final output validates cleanly against MeetingDossier Pydantic model."""
        graph = build_meeting_prep_graph()
        state_in: WorkflowState = {
            "workflow_id": "WF-05",
            "run_id": "run_schema",
            "user_id": "user_01",
            "query": "Chuẩn bị họp",
            "parameters": {"meeting_context": sample_meeting_context},
        }

        res = await graph.ainvoke(state_in)
        dossier_raw = res.get("dossier", {})
        # Must validate against MeetingDossier
        validated = MeetingDossier.model_validate(dossier_raw)
        assert validated.meeting_id == "evt_p19_001"
        assert validated.scheduled_time is not None
        assert validated.scheduled_time.tzinfo is not None  # Aware datetime invariant
        assert len(validated.attendees) == 2
        assert len(validated.suggested_talking_points) > 0


class TestHarnessDispatcherIntegration:
    @pytest.mark.asyncio
    async def test_meeting_prep_full_e2e_via_dispatcher(
        self,
        base_assistant_state: AssistantState,
        sample_meeting_context: dict[str, Any],
    ) -> None:
        """Full end-to-end dispatch of WF-05 through HarnessDispatcher."""
        dispatcher = HarnessDispatcher()
        decision = RouteDecision(
            route_type=RouteType.STATIC_WORKFLOW,
            target_workflow_id="WF-05",
            confidence=0.95,
            domains=[Domain.CALENDAR, Domain.COMMUNICATION, Domain.KNOWLEDGE_RESEARCH],
            parameters={
                "query": "Chuẩn bị tài liệu cuộc họp AI 2026",
                "meeting_context": sample_meeting_context,
            },
            reasoning="Matched WF-05 meeting prep static workflow",
        )

        res = await dispatcher.dispatch(decision, base_assistant_state)
        assert res.get("status") in ("completed", "dossier_synthesized")
        assert "dossier" in res
        dossier = res["dossier"]
        assert dossier["meeting_id"] == "evt_p19_001"
        assert len(dossier["attendees"]) == 2

    def test_default_registry_contains_wf05(self) -> None:
        """Ensure load_default_workflow_registry registers WF-05 with correct domains."""
        registry = load_default_workflow_registry()
        entry = registry.get("WF-05")
        assert entry is not None
        assert entry.workflow_id == "WF-05"
        assert entry.name == "Meeting Prep Graph"
        assert Domain.CALENDAR in entry.domains
        assert Domain.COMMUNICATION in entry.domains
        assert Domain.KNOWLEDGE_RESEARCH in entry.domains

        # Test trigger pattern lookup
        match = registry.match("chuẩn bị hồ sơ họp ban giám đốc")
        assert match is not None
        assert match.workflow_id == "WF-05"


class TestComparativeBenchmarkValidation:
    @pytest.mark.asyncio
    async def test_meeting_prep_benchmark_validation(self) -> None:
        """Validate all quantitative P19-04 thresholds across 20 synthetic scenarios:

        - Latency reduction >= 30%
        - LLM call reduction >= 50%
        - Parallel efficiency == 100%
        - Topology deviation == 0%
        - Citation completeness >= 95%
        """
        comparison = await run_meeting_prep_benchmark()

        assert comparison.total_scenarios == 20
        assert comparison.latency_reduction_percent >= 30.0, (
            f"Latency reduction {comparison.latency_reduction_percent}% < 30%"
        )
        assert comparison.llm_calls_reduction_percent >= 50.0, (
            f"LLM calls reduction {comparison.llm_calls_reduction_percent}% < 50%"
        )
        assert comparison.parallel_efficiency_percent == 100.0, (
            f"Parallel efficiency {comparison.parallel_efficiency_percent}% != 100%"
        )
        assert comparison.topology_deviation_rate == 0.0, (
            f"Topology deviation {comparison.topology_deviation_rate}% != 0%"
        )
        assert comparison.citation_completeness_percent >= 95.0, (
            f"Citation completeness {comparison.citation_completeness_percent}% < 95%"
        )
        assert comparison.passed_all_targets is True

    @pytest.mark.asyncio
    async def test_error_handling_and_partial_error_propagation(
        self, sample_meeting_context: dict[str, Any]
    ) -> None:
        """Verify error propagation from calendar, email, doc research, and synthesizer."""

        # 1. Calendar error
        def failing_cal(ctx: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("Google Calendar API 503")

        graph_cal_err = build_meeting_prep_graph(calendar_finder=failing_cal)
        res_cal = await graph_cal_err.ainvoke(
            {
                "workflow_id": "WF-05",
                "run_id": "r_cal_err",
                "user_id": "u1",
                "query": "Họp Q3",
            }
        )
        assert res_cal["status"] == "calendar_fetch_error"
        assert any("503" in e for e in res_cal.get("errors", []))

        # 2. Branch research errors
        def failing_email(ctx: dict[str, Any]) -> list[dict[str, Any]]:
            raise RuntimeError("Gmail rate limit")

        def failing_doc(ctx: dict[str, Any]) -> list[dict[str, Any]]:
            raise RuntimeError("Drive quota exceeded")

        graph_branch_err = build_meeting_prep_graph(
            email_researcher=failing_email,
            doc_researcher=failing_doc,
        )
        res_branch = await graph_branch_err.ainvoke(
            {
                "workflow_id": "WF-05",
                "run_id": "r_branch_err",
                "user_id": "u1",
                "query": "Họp Q3",
                "parameters": {"meeting_context": sample_meeting_context},
            }
        )
        assert res_branch["status"] == "partial_error"
        dossier = res_branch.get("dossier", {})
        assert dossier.get("status") == "partial_error"
        assert len(res_branch.get("errors", [])) >= 2

    @pytest.mark.asyncio
    async def test_custom_synthesizers_and_event_id_fallback(self) -> None:
        """Verify custom synthesizer (returning MeetingDossier or dict) and event_id parameter fallback."""

        def custom_dossier_synth(ctx: dict[str, Any]) -> MeetingDossier:
            return MeetingDossier(
                meeting_id="custom_dossier_01",
                event_summary="Custom Dossier Meeting",
                scheduled_time=datetime(2026, 9, 10, 15, 0, tzinfo=UTC),
                attendees=[MeetingAttendee(email="test@corp.vn", name="Test User")],
                recent_discussions=["Custom thread summary"],
                relevant_documents=[
                    MeetingDocumentRef(citation_id="doc_custom", title="Custom Spec")
                ],
                suggested_talking_points=["Point A", "Point B"],
                unresolved_action_items=["Item 1"],
                status="completed",
            )

        graph_synth = build_meeting_prep_graph(synthesizer=custom_dossier_synth)
        res1 = await graph_synth.ainvoke(
            {
                "workflow_id": "WF-05",
                "run_id": "r_custom_synth",
                "user_id": "u1",
                "query": "Họp",
                "parameters": {
                    "event_id": "evt_from_params",
                    "title": "Họp trực tiếp",
                    "start_time": datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
                    "attendees": ["test@corp.vn"],
                },
            }
        )
        assert res1["status"] == "completed"
        assert res1["dossier"]["meeting_id"] == "custom_dossier_01"

        # Dict synthesizer
        def dict_synth(ctx: dict[str, Any]) -> dict[str, Any]:
            return {
                "meeting_id": "dict_id_99",
                "event_summary": "Dict Summary",
                "recent_discussions": ["Discussion via dict"],
            }

        graph_dict = build_meeting_prep_graph(synthesizer=dict_synth)
        res2 = await graph_dict.ainvoke(
            {
                "workflow_id": "WF-05",
                "run_id": "r_dict_synth",
                "user_id": "u1",
                "query": "Họp",
                "parameters": {
                    "event_id": "evt_dict",
                    "title": "Họp Dict",
                    "attendees": ["dict@corp.vn"],
                },
            }
        )
        assert res2["dossier"]["meeting_id"] == "dict_id_99"

        # Broken synthesizer fails closed to partial_error
        def broken_synth(ctx: dict[str, Any]) -> dict[str, Any]:
            raise ValueError("Synthesizer model parsing failure")

        graph_broken = build_meeting_prep_graph(synthesizer=broken_synth)
        res3 = await graph_broken.ainvoke(
            {
                "workflow_id": "WF-05",
                "run_id": "r_broken_synth",
                "user_id": "u1",
                "query": "Họp",
                "parameters": {
                    "event_id": "evt_broken",
                    "title": "Họp Broken",
                    "attendees": ["broken@corp.vn"],
                },
            }
        )
        assert res3["status"] == "partial_error"
