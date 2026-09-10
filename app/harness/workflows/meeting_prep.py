"""WF-05: Meeting Prep Graph (P19 / MASTER_PLAN §5.3 & §18A.5).

Hardened static StateGraph workflow for deterministic meeting preparation:
1. identify_meeting: Locate calendar event matching the query (fail-closed if none).
2. resolve_context: Extract attendees, agenda topics, and reference dates.
3. dispatch_research_branches: Parallel fan-out via LangGraph Send API:
   - email_research (Node 3A): Search recent communication and action items (read-only).
   - document_research (Node 3B): Retrieve internal documents, specs, contracts (read-only).
4. synthesize_dossier: Fan-in Reducer compiling evidence into canonical MeetingDossier.

Invariants:
- Parallelism (§13): Nodes 3A and 3B execute concurrently via Send API.
- Least-Privilege Isolation: Read-only access only; no mutation tools exposed.
- Deterministic Topology: 4-stage DAG with zero supervisor replanning overhead.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from app.domain.models.supervisor.meeting_dossier import (
    MeetingAttendee,
    MeetingDocumentRef,
    MeetingDossier,
)
from app.harness.workflow_channels import WorkflowState
from app.harness.workflows.meeting_prep_policy import (
    FORBIDDEN_MUTATION_TOOLS,
    assert_read_only_tool,
)

# Re-export for tests and runtime isolation checks.
__all__ = [
    "FORBIDDEN_MUTATION_TOOLS",
    "assert_read_only_tool",
    "build_meeting_prep_graph",
]


def _call_adapter(adapter: Any, context: dict[str, Any]) -> Any:
    """Invoke adapter function supporting either dict context or string query signature."""
    try:
        return adapter(context)
    except TypeError:
        return adapter(context.get("query", ""))


def build_meeting_prep_graph(
    *,
    calendar_finder: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    email_researcher: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
    doc_researcher: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
    synthesizer: Callable[[dict[str, Any]], MeetingDossier | dict[str, Any]] | None = None,
    checkpointer: Any | None = None,
    allowed_tools: Iterable[str] | None = None,
) -> Any:
    """Compile the WF-05 Meeting Prep StateGraph."""
    if allowed_tools is not None:
        for tool_name in allowed_tools:
            assert_read_only_tool(tool_name)

    def _identify_meeting(state: WorkflowState) -> dict[str, Any]:
        """Node 1: Locate target meeting event in user calendar."""
        query = state.get("query", "")
        params = state.get("parameters", {})
        ctx: dict[str, Any] | None = None

        if calendar_finder is not None:
            try:
                ctx = _call_adapter(
                    calendar_finder,
                    {"query": query, "user_id": state.get("user_id", ""), **params},
                )
            except Exception as exc:  # noqa: BLE001
                err = f"Calendar lookup error: {exc}"
                return {
                    "errors": [err],
                    "status": "calendar_fetch_error",
                    "meeting_context": {},
                }
        elif "meeting_context" in params and params["meeting_context"]:
            ctx = params["meeting_context"]
        elif "event_id" in params:
            ctx = {
                "event_id": params["event_id"],
                "title": params.get("title", f"Meeting on {query}"),
                "attendees": params.get("attendees", []),
                "summary": params.get("summary", ""),
                "start_time": params.get("start_time"),
            }

        # Graceful exit if no meeting is found
        if not ctx or not ctx.get("event_id") and not ctx.get("title"):
            return {
                "meeting_context": {},
                "status": "no_meeting_found",
                "output": {
                    "status": "no_meeting_found",
                    "message": f"Không tìm thấy cuộc họp nào phù hợp với yêu cầu: '{query}'.",
                },
            }

        return {
            "meeting_context": ctx,
            "status": "meeting_identified",
        }

    def _resolve_context(state: WorkflowState) -> dict[str, Any]:
        """Node 2: Extract attendee profiles, agenda keywords, and meeting context."""
        curr_status = state.get("status")
        if curr_status in ("calendar_fetch_error", "no_meeting_found", "failed", "error"):
            return {"status": curr_status}

        meeting_ctx = state.get("meeting_context", {})
        if not meeting_ctx:
            return {"status": "no_meeting_found"}

        raw_attendees = meeting_ctx.get("attendees", [])
        normalized_attendees: list[dict[str, Any]] = []

        for att in raw_attendees:
            if isinstance(att, str):
                email = att.strip()
                name = email.split("@")[0].capitalize()
                normalized_attendees.append({"email": email, "name": name})
            elif isinstance(att, dict):
                email = att.get("email", "").strip()
                name = att.get("name") or (email.split("@")[0].capitalize() if email else None)
                normalized_attendees.append(
                    {
                        "email": email,
                        "name": name,
                        "role": att.get("role"),
                        "organization": att.get("organization"),
                    }
                )

        summary = meeting_ctx.get("summary") or meeting_ctx.get("title", "")
        # Extract potential agenda topics from title and summary
        topics = [t.strip() for t in re.split(r"[,;:\-–—\n]", summary) if len(t.strip()) > 3]

        enriched_ctx = dict(meeting_ctx)
        enriched_ctx["resolved_attendees"] = normalized_attendees
        enriched_ctx["agenda_topics"] = topics

        return {
            "meeting_context": enriched_ctx,
            "status": "context_resolved",
        }

    def _dispatch_research_branches(state: WorkflowState) -> list[Send] | str:
        """Conditional branch: Send concurrent payloads to email and doc research."""
        status = state.get("status")
        if status in ("no_meeting_found", "calendar_fetch_error", "failed"):
            return END

        meeting_ctx = state.get("meeting_context", {})
        if not meeting_ctx:
            return END

        query = state.get("query", "")
        user_id = state.get("user_id", "")
        run_id = state.get("run_id", "")

        payload = {
            "query": query,
            "user_id": user_id,
            "run_id": run_id,
            "meeting_context": meeting_ctx,
        }

        # Fan-out concurrently via LangGraph Send API
        return [
            Send("email_research", payload),
            Send("document_research", payload),
        ]

    def _email_research(state: WorkflowState) -> dict[str, Any]:
        """Node 3A: Read-only communication research (emails, commitments)."""
        ctx = state.get("meeting_context", {})
        attendees = ctx.get("resolved_attendees", [])
        query = state.get("query", "")
        user_id = state.get("user_id", "")
        run_id = state.get("run_id", "")

        email_items: list[dict[str, Any]] = []
        branch_errors: list[str] = []

        if email_researcher is not None:
            try:
                email_items = _call_adapter(
                    email_researcher,
                    {
                        "query": query,
                        "user_id": user_id,
                        "run_id": run_id,
                        "attendees": attendees,
                        "meeting_context": ctx,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                err = f"Email research error: {exc}"
                branch_errors.append(err)
        # When adapter is absent, return empty list (never fabricate placeholder evidence per H2)

        return {
            "branch_results": [
                {
                    "domain": "email",
                    "items": email_items,
                    "user_id": user_id,
                }
            ],
            "branch_errors": branch_errors,
        }

    def _document_research(state: WorkflowState) -> dict[str, Any]:
        """Node 3B: Read-only knowledge research (RAG, Drive)."""
        ctx = state.get("meeting_context", {})
        query = state.get("query", "")
        user_id = state.get("user_id", "")
        run_id = state.get("run_id", "")

        doc_items: list[dict[str, Any]] = []
        branch_errors: list[str] = []

        if doc_researcher is not None:
            try:
                doc_items = _call_adapter(
                    doc_researcher,
                    {
                        "query": query,
                        "user_id": user_id,
                        "run_id": run_id,
                        "topics": ctx.get("agenda_topics", []),
                        "meeting_context": ctx,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                err = f"Document research error: {exc}"
                branch_errors.append(err)
        # When adapter is absent, return empty list (never fabricate placeholder evidence per H2)

        return {
            "branch_results": [
                {
                    "domain": "document",
                    "items": doc_items,
                    "user_id": user_id,
                }
            ],
            "branch_errors": branch_errors,
        }

    def _synthesize_dossier(state: WorkflowState) -> dict[str, Any]:
        """Node 4: Fan-in Reducer compiling evidence into structured MeetingDossier."""
        meeting_ctx = state.get("meeting_context", {})
        branch_results = state.get("branch_results", [])
        branch_errors = state.get("branch_errors", [])
        errors = state.get("errors", [])
        all_errors = list(dict.fromkeys([*errors, *branch_errors]))

        # Collect email discussions & document references
        recent_discussions: list[str] = []
        relevant_docs: list[MeetingDocumentRef] = []

        for branch in branch_results:
            domain = branch.get("domain")
            items = branch.get("items", [])
            if domain == "email":
                for it in items:
                    sender = it.get("from") or it.get("sender", "Unknown")
                    snippet = it.get("snippet") or it.get("subject", "")
                    commitments = it.get("commitments", [])
                    line = f"Email từ {sender}: {snippet}"
                    if commitments:
                        line += f" (Cam kết: {', '.join(commitments)})"
                    recent_discussions.append(line)
            elif domain in ("document", "knowledge", "drive", "rag"):
                for it in items:
                    cid = it.get("citation_id") or it.get("id") or f"doc_{len(relevant_docs) + 1}"
                    title = it.get("title") or it.get("name", "Tài liệu")
                    snippet = it.get("snippet") or it.get("content")
                    relevant_docs.append(
                        MeetingDocumentRef(
                            citation_id=cid,
                            title=title,
                            domain=domain,
                            snippet=snippet,
                        )
                    )

        # Build attendees
        attendees_models: list[MeetingAttendee] = []
        for att in meeting_ctx.get("resolved_attendees", []):
            attendees_models.append(
                MeetingAttendee(
                    email=att["email"],
                    name=att.get("name"),
                    role=att.get("role"),
                    organization=att.get("organization"),
                )
            )

        # Parse scheduled time (None if missing or unparseable per M3)
        raw_start = meeting_ctx.get("start_time") or meeting_ctx.get("scheduled_time")
        scheduled_time: datetime | None = None
        if isinstance(raw_start, datetime):
            scheduled_time = raw_start if raw_start.tzinfo else raw_start.replace(tzinfo=UTC)
        elif isinstance(raw_start, str) and raw_start:
            try:
                parsed_dt = datetime.fromisoformat(raw_start)
                scheduled_time = parsed_dt if parsed_dt.tzinfo else parsed_dt.replace(tzinfo=UTC)
            except ValueError:
                scheduled_time = None
                all_errors.append(f"Invalid scheduled_time string: '{raw_start}'")
        # Extract topics and suggested talking points
        agenda_topics = meeting_ctx.get("agenda_topics", [])
        suggested_points = [f"Thảo luận mục tiêu: {t}" for t in agenda_topics] or [
            "Đánh giá tiến độ công việc gần nhất",
            "Thống nhất các cam kết và bước triển khai tiếp theo",
        ]

        unresolved = [
            "Làm rõ quyền sở hữu các đầu việc còn tồn đọng từ các trao đổi gần nhất",
        ]

        if scheduled_time is None:
            all_errors.append("No valid scheduled_time provided in meeting context.")

        final_status = "partial_error" if all_errors else "completed"

        if synthesizer is not None:
            try:
                synth_out = synthesizer(
                    {
                        "meeting_context": meeting_ctx,
                        "branch_results": branch_results,
                        "branch_errors": all_errors,
                        "attendees": attendees_models,
                        "recent_discussions": recent_discussions,
                        "relevant_documents": relevant_docs,
                    }
                )
                if isinstance(synth_out, MeetingDossier):
                    dossier = synth_out
                elif isinstance(synth_out, dict):
                    dossier = MeetingDossier(
                        meeting_id=synth_out.get(
                            "meeting_id", meeting_ctx.get("event_id", "evt_unknown")
                        ),
                        event_summary=synth_out.get("event_summary", meeting_ctx.get("title", "")),
                        scheduled_time=scheduled_time,
                        attendees=attendees_models,
                        recent_discussions=synth_out.get("recent_discussions", recent_discussions),
                        relevant_documents=synth_out.get("relevant_documents", relevant_docs),
                        suggested_talking_points=synth_out.get(
                            "suggested_talking_points", suggested_points
                        ),
                        unresolved_action_items=synth_out.get(
                            "unresolved_action_items", unresolved
                        ),
                        status=final_status,
                    )
                else:
                    raise TypeError("Synthesizer must return MeetingDossier or dict")
            except Exception as exc:  # noqa: BLE001
                all_errors.append(f"Synthesizer error: {exc}")
                final_status = "partial_error"
                dossier = MeetingDossier(
                    meeting_id=meeting_ctx.get("event_id", "evt_unknown"),
                    event_summary=meeting_ctx.get("title")
                    or meeting_ctx.get("summary", "Cuộc họp"),
                    scheduled_time=scheduled_time,
                    attendees=attendees_models,
                    recent_discussions=recent_discussions,
                    relevant_documents=relevant_docs,
                    suggested_talking_points=suggested_points,
                    unresolved_action_items=unresolved,
                    status=final_status,
                )
        else:
            dossier = MeetingDossier(
                meeting_id=meeting_ctx.get("event_id", "evt_unknown"),
                event_summary=meeting_ctx.get("title") or meeting_ctx.get("summary", "Cuộc họp"),
                scheduled_time=scheduled_time,
                attendees=attendees_models,
                recent_discussions=recent_discussions,
                relevant_documents=relevant_docs,
                suggested_talking_points=suggested_points,
                unresolved_action_items=unresolved,
                status=final_status,
            )

        output_dict = dossier.model_dump(mode="json")

        return {
            "dossier": output_dict,
            "output": output_dict,
            "status": final_status,
            "errors": all_errors,
        }

    # Assemble StateGraph
    builder: StateGraph[WorkflowState, Any, Any, Any] = StateGraph(WorkflowState)
    builder.add_node("identify_meeting", _identify_meeting)
    builder.add_node("resolve_context", _resolve_context)
    builder.add_node("email_research", _email_research)
    builder.add_node("document_research", _document_research)
    builder.add_node("synthesize_dossier", _synthesize_dossier)

    builder.set_entry_point("identify_meeting")
    builder.add_edge("identify_meeting", "resolve_context")
    builder.add_conditional_edges(
        "resolve_context",
        _dispatch_research_branches,
        ["email_research", "document_research", END],
    )
    builder.add_edge("email_research", "synthesize_dossier")
    builder.add_edge("document_research", "synthesize_dossier")
    builder.add_edge("synthesize_dossier", END)

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
