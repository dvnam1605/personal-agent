"""Unit tests for HarnessDispatcher (spec P15 / N2)."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.enums import Domain, RouteType
from app.domain.errors import ConfigurationError, ValidationError
from app.domain.models import AssistantState, RouteDecision
from app.harness.dispatch import HarnessDispatcher
from app.harness.workflows.document_briefing import build_document_briefing_graph


@pytest.fixture
def base_state() -> AssistantState:
    return AssistantState(
        user_id="user_test_01",
        request="test request",
        goal="test goal",
    )


@pytest.mark.asyncio
class TestHarnessDispatcher:
    async def test_dispatch_wf01_needs_approval_without_token(
        self, base_state: AssistantState
    ) -> None:
        """WF-01 returns status='needs_approval' and draft_id=None when approval_token is missing."""
        dispatcher = HarnessDispatcher()
        decision = RouteDecision(
            route_type=RouteType.STATIC_WORKFLOW,
            target_workflow_id="WF-01",
            confidence=1.0,
            domains=[Domain.CALENDAR, Domain.COMMUNICATION],
            parameters={
                "query": "Soạn email cảm ơn sau họp",
                "meeting_context": {
                    "event_id": "evt_123",
                    "title": "Quarterly Review",
                    "attendees": ["alice@example.com", "bob@example.com"],
                },
            },
            reasoning="Matched WF-01 meeting follow-up trigger",
        )

        res = await dispatcher.dispatch(decision, base_state)
        assert res.get("status") == "needs_approval"
        assert res.get("draft_id") is None
        assert "Pending user approval" in res.get("draft_content", "")
        output = res.get("output", {})
        assert output.get("draft_id") is None
        assert output.get("status") == "needs_approval"

    async def test_dispatch_wf01_token_in_extra_parameters_merged(
        self, base_state: AssistantState
    ) -> None:
        """Caller supplying approval_token via dispatch parameters merges safely."""
        created_drafts: list[dict[str, Any]] = []

        def fake_draft_creator(ctx: dict[str, Any]) -> dict[str, Any]:
            created_drafts.append(ctx)
            return {"draft_id": "draft_abc", "status": "draft_created"}

        from app.harness.workflows.meeting_followup import build_meeting_followup_graph

        graph = build_meeting_followup_graph(draft_creator=fake_draft_creator)
        dispatcher = HarnessDispatcher(wf01_builder=lambda **k: graph)

        decision = RouteDecision(
            route_type=RouteType.STATIC_WORKFLOW,
            target_workflow_id="WF-01",
            confidence=1.0,
            domains=[Domain.CALENDAR, Domain.COMMUNICATION],
            parameters={
                "query": "Soạn email sau họp",
                "meeting_context": {
                    "event_id": "evt_123",
                    "title": "Quarterly Review",
                    "attendees": ["alice@example.com"],
                },
            },
            reasoning="Matched WF-01",
        )

        # Token passed in parameters dict at dispatch time
        res = await dispatcher.dispatch(
            decision,
            base_state,
            {"approval_token": "valid_token_xyz"},
        )
        assert res.get("status") == "draft_created"
        assert res.get("draft_id") == "draft_abc"
        assert res.get("output", {}).get("draft_id") == "draft_abc"
        assert len(created_drafts) == 1
        assert created_drafts[0]["approval_token"] == "valid_token_xyz"

    async def test_dispatch_wf02_partial_error_propagation(
        self, base_state: AssistantState
    ) -> None:
        """WF-02 returns partial_error when one search branch succeeds and another fails."""
        def failing_drive(ctx: dict[str, Any]) -> list[dict[str, Any]]:
            raise RuntimeError("Google Drive API rate limit exceeded")

        def working_rag(ctx: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {
                    "title": "Chính sách bảo mật 2026",
                    "citation_id": "doc_rag_01",
                    "content": "Nội dung quy định",
                }
            ]

        graph = build_document_briefing_graph(
            rag_searcher=working_rag,
            drive_searcher=failing_drive,
        )
        dispatcher = HarnessDispatcher(wf02_builder=lambda **k: graph)

        decision = RouteDecision(
            route_type=RouteType.STATIC_WORKFLOW,
            target_workflow_id="WF-02",
            confidence=0.95,
            domains=[Domain.KNOWLEDGE_RESEARCH],
            parameters={"query": "Tìm tài liệu chính sách bảo mật"},
            reasoning="Matched WF-02 briefing trigger",
        )

        res = await dispatcher.dispatch(decision, base_state)
        assert res.get("status") == "partial_error"
        output = res.get("output", {})
        assert output.get("status") == "partial_error"
        assert output.get("evidence_count") == 1
        assert output.get("error_count") >= 1
        assert len(res.get("citations", [])) == 1

    async def test_dispatch_unknown_workflow_raises_configuration_error(
        self, base_state: AssistantState
    ) -> None:
        """Dispatching an unknown target_workflow_id raises ConfigurationError."""
        dispatcher = HarnessDispatcher()
        decision = RouteDecision(
            route_type=RouteType.STATIC_WORKFLOW,
            target_workflow_id="WF-99",
            confidence=0.8,
            domains=[Domain.GENERAL],
            parameters={"query": "Some query"},
            reasoning="Unknown workflow",
        )

        with pytest.raises(ConfigurationError) as exc_info:
            await dispatcher.dispatch(decision, base_state)
        assert "Unknown static workflow_id 'WF-99'" in str(exc_info.value)

    async def test_dispatch_missing_query_raises_validation_error(
        self, base_state: AssistantState
    ) -> None:
        """Dispatching without a query parameter raises ValidationError."""
        dispatcher = HarnessDispatcher()
        decision = RouteDecision(
            route_type=RouteType.STATIC_WORKFLOW,
            target_workflow_id="WF-01",
            confidence=1.0,
            domains=[Domain.CALENDAR, Domain.COMMUNICATION],
            parameters={},
            reasoning="Missing query",
        )

        with pytest.raises(ValidationError) as exc_info:
            await dispatcher.dispatch(decision, base_state)
        assert "non-empty query" in str(exc_info.value)

    async def test_dispatch_terminal_routes(
        self, base_state: AssistantState
    ) -> None:
        """Immediate terminal routes (REJECT, CLARIFICATION, CASUAL_RESPONSE, DIRECT_SPECIALIST, SUPERVISOR_DAG)."""
        dispatcher = HarnessDispatcher()

        # REJECT
        rej = RouteDecision(
            route_type=RouteType.REJECT,
            confidence=1.0,
            domains=[Domain.GENERAL],
            reasoning="Safety policy block",
            reason_code="SAFETY_BLOCK",
        )
        assert (await dispatcher.dispatch(rej, base_state)) == {
            "status": "rejected",
            "reason": "Safety policy block",
            "reason_code": "SAFETY_BLOCK",
        }

        # CLARIFICATION
        clar = RouteDecision(
            route_type=RouteType.CLARIFICATION,
            confidence=0.5,
            domains=[Domain.GENERAL],
            reasoning="Query ambiguous",
            reason_code="AMBIGUOUS",
        )
        assert (await dispatcher.dispatch(clar, base_state)) == {
            "status": "clarification_needed",
            "reason": "Query ambiguous",
            "reason_code": "AMBIGUOUS",
        }

        # CASUAL_RESPONSE
        cas = RouteDecision(
            route_type=RouteType.CASUAL_RESPONSE,
            confidence=0.99,
            domains=[Domain.GENERAL],
            reasoning="Xin chào bạn! Tôi có thể giúp gì?",
        )
        assert (await dispatcher.dispatch(cas, base_state)) == {
            "status": "casual_response",
            "message": "Xin chào bạn! Tôi có thể giúp gì?",
        }

        # DIRECT_SPECIALIST
        spec = RouteDecision(
            route_type=RouteType.DIRECT_SPECIALIST,
            target_agent="calendar_specialist",
            confidence=0.9,
            domains=[Domain.CALENDAR],
            parameters={"query": "Lịch họp ngày mai"},
            reasoning="Calendar request",
        )
        res_spec = await dispatcher.dispatch(spec, base_state)
        assert res_spec["status"] == "dispatched_specialist"
        assert res_spec["target_agent"] == "calendar_specialist"

        # SUPERVISOR_DAG
        sup = RouteDecision(
            route_type=RouteType.SUPERVISOR_DAG,
            confidence=0.85,
            domains=[Domain.CALENDAR, Domain.COMMUNICATION],
            parameters={"query": "Lên lịch và gửi email"},
            reasoning="Multi-domain request",
        )
        res_sup = await dispatcher.dispatch(sup, base_state)
        assert res_sup["status"] == "pending_supervisor_orchestration"
        assert res_sup["phase"] == "P16"
