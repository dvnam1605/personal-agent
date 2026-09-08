"""Unit tests for StaticWorkflowRegistry and compiled StateGraph subgraphs (P15).

Verifies:
- Workflow registration, trigger matching (contiguous / regex, no loose subset),
  whitespace stripping, domain preservation on strip, and duplicate rejection (H3, L2, L3).
- Default registry loaded with correct domains (M5).
- WF-01 fail-closed behavior: no fake drafts or attendee fabrication when unconfigured (H2).
- WF-01 approval token requirement:
  - no token -> needs_approval, draft_creator is NOT called (H2).
  - token without tool -> approved_unexecuted, draft_id is None (no fake draft_id) (H2).
  - token with tool -> draft_created with real tool result (H2).
- WF-02 parallel Send API with tenant isolation (user_id, run_id) (M3).
- WF-02 concurrent timing / execution overlap (L1).
- WF-02 pending_domains filtering (M5).
- Reducers accumulation for branch_results and errors (L3).
- Circuit breaker / edge cases with empty or non-string inputs (L2).
"""

import time
from typing import Any

import pytest

from app.domain.enums.enums import Domain
from app.domain.errors import ConfigurationError, ValidationError
from app.harness.workflow_channels import WorkflowState
from app.harness.workflows import (
    build_document_briefing_graph,
    build_meeting_followup_graph,
)
from app.services.workflow_registry import (
    StaticWorkflowEntry,
    StaticWorkflowRegistry,
    load_default_workflow_registry,
    match_workflow_trigger,
)

# ----------------------------------------------------------------------
# Registration & Lookup Tests (L2, L3, H3)
# ----------------------------------------------------------------------


def test_static_workflow_registration_and_match() -> None:
    """Register workflows, test lookup, whitespace normalization, and duplicate rejection."""
    registry = StaticWorkflowRegistry()
    entry = StaticWorkflowEntry(
        workflow_id="  WF-TEST  ",  # tests L3 whitespace trimming
        name="Test Workflow",
        description="A test static workflow.",
        trigger_patterns=("test workflow", "chay workflow test"),
        graph_factory=lambda: None,
        domains=(Domain.CALENDAR, Domain.COMMUNICATION),
    )

    registry.register(entry)
    # Stored and retrievable by stripped ID, domains preserved (L2)
    res = registry.get("WF-TEST")
    assert res is not None
    assert res.workflow_id == "WF-TEST"
    assert res.domains == (Domain.CALENDAR, Domain.COMMUNICATION)
    assert registry.get("non-existent") is None

    # Duplicate registration is rejected
    with pytest.raises(ConfigurationError, match="already registered"):
        registry.register(entry)

    # Blank ID rejected
    with pytest.raises(ValidationError):
        registry.register(
            StaticWorkflowEntry(
                workflow_id="   ",
                name="Bad",
                description="Bad",
                trigger_patterns=(),
                graph_factory=lambda: None,
            )
        )


def test_match_workflow_trigger_contiguous_vs_subset() -> None:
    """Trigger matching requires contiguous phrase or regex, not token-subset (H3)."""
    # Contiguous matches
    assert match_workflow_trigger("meeting follow-up", "Please run meeting follow-up now") is True
    assert match_workflow_trigger("soan follow-up", "Hãy soạn follow-up cho cuộc họp") is True

    # Token subset scattered across sentence must NOT match (H3 fix)
    assert (
        match_workflow_trigger(
            "meeting follow-up",
            "Please follow up after the meeting with Nam",
        )
        is False
    )

    # Regex triggers
    assert match_workflow_trigger(r"/email\s+summary/", "Need an email summary please") is True
    assert match_workflow_trigger(r"/email\s+summary/", "Email the summary") is False

    # Circuit breaker / blank
    assert match_workflow_trigger("test", "") is False
    assert match_workflow_trigger("test", "   ") is False


def test_default_workflow_registry_domains_and_entries() -> None:
    """Default registry contains WF-01 and WF-02 with correct domains and metadata (M5, L1)."""
    registry = load_default_workflow_registry()
    workflows = {w.workflow_id: w for w in registry.list_all()}

    assert "WF-01" in workflows
    assert "WF-02" in workflows

    wf01 = workflows["WF-01"]
    assert wf01.name == "Quick Meeting Follow-up"
    assert Domain.CALENDAR in wf01.domains
    assert Domain.COMMUNICATION in wf01.domains
    assert wf01.metadata.get("adr_reference") == "ADR 0005"

    wf02 = workflows["WF-02"]
    assert wf02.name == "Document Search & Briefing"
    assert Domain.KNOWLEDGE_RESEARCH in wf02.domains
    assert wf02.metadata.get("adr_reference") == "ADR 0005"


# ----------------------------------------------------------------------
# WF-01 Tests: Fail-closed & Approval Governance (H2)
# ----------------------------------------------------------------------


def test_wf01_unconfigured_fails_closed_without_faking_draft() -> None:
    """WF-01 without tools or context fails closed; does NOT fabricate draft or attendees (H2)."""
    graph = build_meeting_followup_graph()

    result = graph.invoke({"query": "secret meeting", "user_id": "real-user-123"})

    assert result["status"] == "failed"
    assert result["output"]["status"] == "failed"
    assert result["output"]["draft_id"] is None
    # Must NOT fabricate attendees like nam@example.com
    assert result.get("meeting_context", {}).get("attendees") in (None, [])
    # Errors channel recorded the failure
    assert len(result.get("errors", [])) > 0
    assert any("No calendar fetcher configured" in err for err in result["errors"])


def test_wf01_calendar_fetch_error_halts_downstream_cleanly() -> None:
    """When calendar_fetcher raises an error, downstream nodes stop cleanly without fabricating (L2)."""
    email_fetcher_called = False
    draft_creator_called = False

    def failing_cal(_ctx: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("Google Calendar API 503 Service Unavailable")

    def mock_emails(_ctx: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal email_fetcher_called
        email_fetcher_called = True
        return [{"from": "client@corp.com", "subject": "Re: Meeting"}]

    def mock_draft(_ctx: dict[str, Any]) -> dict[str, Any]:
        nonlocal draft_creator_called
        draft_creator_called = True
        return {"draft_id": "bad_draft", "status": "draft_created"}

    graph = build_meeting_followup_graph(
        calendar_fetcher=failing_cal,
        email_fetcher=mock_emails,
        draft_creator=mock_draft,
    )

    result = graph.invoke({"query": "Weekly sync", "user_id": "u-123"})

    assert result["status"] == "failed"
    assert result["output"]["status"] == "failed"
    assert result["output"]["draft_id"] is None
    assert email_fetcher_called is False
    assert draft_creator_called is False
    assert any("Calendar fetch failed" in err for err in result.get("errors", []))


def test_wf01_tool_without_token_fails_closed_to_needs_approval() -> None:
    """When draft_creator is injected but approval_token is missing, tool is NOT called (H2)."""
    tool_called = False

    def mock_creator(_ctx: dict[str, Any]) -> dict[str, Any]:
        nonlocal tool_called
        tool_called = True
        return {"draft_id": "leaked_draft_id", "status": "draft_created"}

    graph = build_meeting_followup_graph(draft_creator=mock_creator)
    state: WorkflowState = {
        "workflow_id": "WF-01",
        "run_id": "run-test-wf01",
        "user_id": "user-corp-1",
        "query": "Follow up with client",
        "parameters": {
            "meeting_context": {
                "title": "Client Sync",
                "attendees": ["client@corp.com"],
            }
            # Missing approval_token
        },
    }

    result = graph.invoke(state)

    # Tool MUST NOT be called without approval token
    assert tool_called is False
    assert result["status"] == "needs_approval"
    assert result["output"]["status"] == "needs_approval"
    assert result["draft_id"] is None
    assert "Pending user approval" in result["draft_content"]


def test_wf01_token_without_tool_does_not_fabricate_draft_id() -> None:
    """When token is present but no tool is injected, graph does NOT fabricate draft_id (H2)."""
    graph = build_meeting_followup_graph(draft_creator=None)
    state: WorkflowState = {
        "workflow_id": "WF-01",
        "run_id": "run-p15-02",
        "user_id": "user-corp-1",
        "query": "Sync with Client",
        "parameters": {
            "approval_token": "appr-valid-tok-123",
            "meeting_context": {
                "event_id": "evt-client-99",
                "title": "Client Status Sync",
                "attendees": ["client@corp.com"],
                "summary": "Agreed on phase 2 delivery date.",
            },
        },
    }

    result = graph.invoke(state)

    # Without tool injected, MUST NOT fabricate draft_id!
    assert result["draft_id"] is None
    assert result["output"]["draft_id"] is None
    assert result["status"] == "approved_unexecuted"


def test_wf01_tool_with_token_creates_draft() -> None:
    """When token is present AND tool is injected, draft is created via tool (H2)."""

    def mock_creator(_ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "draft_id": "real-draft-789",
            "status": "draft_created",
            "draft_content": "Dear Client, thank you.",
        }

    graph = build_meeting_followup_graph(draft_creator=mock_creator)
    state: WorkflowState = {
        "workflow_id": "WF-01",
        "run_id": "run-p15-03",
        "user_id": "user-corp-1",
        "query": "Sync with Client",
        "parameters": {
            "approval_token": "appr-valid-tok-123",
            "meeting_context": {
                "title": "Client Status Sync",
                "attendees": ["client@corp.com"],
            },
        },
    }

    result = graph.invoke(state)

    assert result["status"] == "draft_created"
    assert result["output"]["status"] == "draft_created"
    assert result["draft_id"] == "real-draft-789"
    assert "Dear Client" in result["draft_content"]


def test_wf01_meeting_followup_with_custom_injected_fetchers() -> None:
    """WF-01 accepts injected fetchers and passes user_id context."""
    received_contexts: list[dict[str, Any]] = []

    def mock_cal(ctx: dict[str, Any]) -> dict[str, Any]:
        received_contexts.append(ctx)
        return {
            "title": "Strategy Sync",
            "attendees": ["ceo@example.com"],
            "summary": "Set Q4 targets.",
        }

    def mock_mail(ctx: dict[str, Any]) -> list[dict[str, Any]]:
        received_contexts.append(ctx)
        return [{"from": "ceo@example.com", "subject": "Priorities", "snippet": "Deliver on time."}]

    def mock_draft(ctx: dict[str, Any]) -> dict[str, Any]:
        received_contexts.append(ctx)
        return {
            "draft_id": "draft-custom-999",
            "draft_content": "Dear CEO, targets noted.",
            "status": "draft_created",
        }

    graph = build_meeting_followup_graph(
        calendar_fetcher=mock_cal,
        email_fetcher=mock_mail,
        draft_creator=mock_draft,
    )

    result = graph.invoke({
        "query": "Strategy Sync",
        "user_id": "user-exec-1",
        "parameters": {"approval_token": "appr-valid-123"},
    })

    assert len(received_contexts) == 3
    # Check tenant isolation context passed to all tools
    assert all(c.get("user_id") == "user-exec-1" for c in received_contexts)
    assert result["draft_id"] == "draft-custom-999"
    assert result["status"] == "draft_created"


# ----------------------------------------------------------------------
# WF-02 Tests: Parallel Send, Tenant Isolation & Concurrency (M3, L1, L2)
# ----------------------------------------------------------------------


def test_wf02_send_tenant_isolation() -> None:
    """WF-02 Send branches pass user_id and run_id to searchers (M3)."""
    rag_context: dict[str, Any] = {}
    drive_context: dict[str, Any] = {}

    def mock_rag(context: Any) -> list[dict[str, Any]]:
        nonlocal rag_context
        if isinstance(context, dict):
            rag_context = context
        return [{"title": "RAG Document", "source": "rag", "citation_id": "c-rag-1"}]

    def mock_drive(context: Any) -> list[dict[str, Any]]:
        nonlocal drive_context
        if isinstance(context, dict):
            drive_context = context
        return [{"name": "Drive Doc", "source": "drive", "citation_id": "c-drive-1"}]

    graph = build_document_briefing_graph(
        rag_searcher=mock_rag,
        drive_searcher=mock_drive,
    )

    initial_state: WorkflowState = {
        "workflow_id": "WF-02",
        "run_id": "run-tenant-xyz",
        "user_id": "user-tenant-abc",
        "query": "Confidential Project Spec",
    }

    result = graph.invoke(initial_state)

    assert result["status"] == "completed"
    # Verify tenant isolation fields were passed to both searchers
    assert rag_context.get("user_id") == "user-tenant-abc"
    assert rag_context.get("run_id") == "run-tenant-xyz"
    assert rag_context.get("query") == "Confidential Project Spec"

    assert drive_context.get("user_id") == "user-tenant-abc"
    assert drive_context.get("run_id") == "run-tenant-xyz"
    assert drive_context.get("query") == "Confidential Project Spec"

    # Reducer accumulated both branches with user_id recorded
    branches = result.get("branch_results", [])
    assert len(branches) == 2
    assert all(b.get("user_id") == "user-tenant-abc" for b in branches)


def test_wf02_concurrency_overlap() -> None:
    """WF-02 Send branches run concurrently, overlapping I/O wait times (L1)."""
    delay = 0.08  # 80ms each

    def slow_rag(_ctx: Any) -> list[dict[str, Any]]:
        time.sleep(delay)
        return [{"title": "Slow RAG Doc", "source": "rag", "citation_id": "rag-1"}]

    def slow_drive(_ctx: Any) -> list[dict[str, Any]]:
        time.sleep(delay)
        return [{"name": "Slow Drive Doc", "source": "drive", "citation_id": "drive-1"}]

    graph = build_document_briefing_graph(
        rag_searcher=slow_rag,
        drive_searcher=slow_drive,
    )

    t0 = time.perf_counter()
    result = graph.invoke({"query": "Concurrent Search", "user_id": "u1"})
    elapsed = time.perf_counter() - t0

    assert result["status"] == "completed"
    assert len(result.get("branch_results", [])) == 2
    # If sequential, elapsed would be >= 2 * delay (0.16s).
    # Concurrency ensures overlapping execution: elapsed must be < delay * 1.6 (0.128s).
    assert elapsed < (delay * 1.6)


def test_wf02_pending_domains_filtering() -> None:
    """WF-02 respects pending_domains; only dispatches requested branches (M5)."""
    called: list[str] = []

    def mock_rag(_ctx: Any) -> list[dict[str, Any]]:
        called.append("rag")
        return [{"title": "RAG Item", "source": "rag", "citation_id": "r1"}]

    def mock_drive(_ctx: Any) -> list[dict[str, Any]]:
        called.append("drive")
        return [{"name": "Drive Item", "source": "drive", "citation_id": "d1"}]

    graph = build_document_briefing_graph(
        rag_searcher=mock_rag,
        drive_searcher=mock_drive,
    )

    # Only request RAG domain
    result = graph.invoke({
        "query": "Only RAG",
        "user_id": "u1",
        "pending_domains": ["rag"],
    })

    assert called == ["rag"]
    assert len(result["branch_results"]) == 1
    assert result["branch_results"][0]["domain"] == "rag"


def test_wf02_branch_error_handling_reducer() -> None:
    """When a branch raises an exception, error is recorded in errors reducer (L3)."""

    def failing_rag(_ctx: Any) -> list[dict[str, Any]]:
        raise ConnectionError("RAG index unavailable")

    def ok_drive(_ctx: Any) -> list[dict[str, Any]]:
        return [{"name": "Doc.pdf", "source": "drive", "citation_id": "d-ok"}]

    graph = build_document_briefing_graph(
        rag_searcher=failing_rag,
        drive_searcher=ok_drive,
    )

    result = graph.invoke({"query": "Resilience test", "user_id": "u1"})

    assert result["status"] == "partial_error"
    assert result["output"]["status"] == "partial_error"
    # Errors channel and branch_errors contain the RAG error message
    assert len(result.get("errors", [])) == 1
    assert "RAG search error: RAG index unavailable" in result["errors"][0]
    assert len(result.get("branch_errors", [])) == 1
    # Drive branch still succeeded and was synthesized
    assert result["output"]["evidence_count"] == 1
    assert result["output"]["error_count"] == 1
