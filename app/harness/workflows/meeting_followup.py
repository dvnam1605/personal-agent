"""WF-01: Quick Meeting Follow-up StateGraph (P15 / §18A.5).

Sequence:
1. fetch_calendar: Retrieve meeting details and attendees (fail-closed if missing).
2. fetch_emails: Retrieve recent emails from meeting participants.
3. draft_followup: Formulate draft follow-up email with approval check.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from app.domain.enums import RunStatus
from app.harness.workflow_channels import WorkflowState
from app.services.approvals import verify_approval_token_sync


def build_meeting_followup_graph(
    *,
    calendar_fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    email_fetcher: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
    draft_creator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    checkpointer: Any | None = None,
) -> Any:
    """Compile the WF-01 Quick Meeting Follow-up StateGraph."""

    def _fetch_calendar(state: WorkflowState) -> dict[str, Any]:
        query = state.get("query", "")
        params = state.get("parameters", {})
        ctx: dict[str, Any] | None = None
        if calendar_fetcher is not None:
            try:
                ctx = calendar_fetcher(
                    {"query": query, "user_id": state.get("user_id", ""), **params}
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "errors": [f"Calendar fetch failed: {exc}"],
                    "status": "calendar_fetch_error",
                }
        elif "meeting_context" in params:
            ctx = params["meeting_context"]
        elif "attendees" in params:
            ctx = {
                "event_id": params.get("event_id", "evt_provided"),
                "title": params.get("title", f"Meeting on {query}"),
                "attendees": params.get("attendees", []),
                "summary": params.get("summary", ""),
            }

        if not ctx:
            return {
                "errors": ["No calendar fetcher configured and no meeting_context supplied."],
                "status": "insufficient_calendar_context",
                "meeting_context": {},
            }
        return {"meeting_context": ctx}

    def _fetch_emails(state: WorkflowState) -> dict[str, Any]:
        if state.get("status") in (
            "insufficient_calendar_context",
            "calendar_fetch_error",
            "failed",
            "error",
        ):
            return {"attendee_messages": []}

        meeting_ctx = state.get("meeting_context", {})
        attendees = meeting_ctx.get("attendees", [])
        if not attendees:
            return {
                "errors": ["No attendees found in meeting context; skipping email retrieval."],
                "attendee_messages": [],
            }

        if email_fetcher is not None:
            try:
                messages = email_fetcher(
                    {
                        "attendees": attendees,
                        "query": state.get("query", ""),
                        "user_id": state.get("user_id", ""),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "errors": [f"Email fetch failed: {exc}"],
                    "attendee_messages": [],
                }
        else:
            messages = []
        return {"attendee_messages": messages}

    def _draft_followup(state: WorkflowState) -> dict[str, Any]:
        curr_status = state.get("status")
        if curr_status in (
            "insufficient_calendar_context",
            "calendar_fetch_error",
            "failed",
            "error",
        ):
            reason = (
                "Missing meeting context."
                if curr_status == "insufficient_calendar_context"
                else f"Calendar retrieval stopped with status: {curr_status}."
            )
            output = {
                "status": "failed",
                "reason": reason,
                "draft_id": None,
            }
            return {"output": output, "status": "failed"}

        meeting_ctx = state.get("meeting_context", {})
        messages = state.get("attendee_messages", [])
        attendees = meeting_ctx.get("attendees", [])
        token = state.get("parameters", {}).get("approval_token")
        run_id = state.get("run_id")
        user_id = state.get("user_id")

        # Peek-only at workflow boundary (consume=False).
        # The single-use token will be consumed exclusively at the tool execution gate
        # (e.g. draft_creator calling require_mutation_approval) to prevent double-consumption.
        token_valid = False
        if token:
            params = state.get("parameters", {})
            draft_args = params.get("arguments") if isinstance(params, dict) else None
            if isinstance(draft_args, dict):
                from app.services.approvals import canonical_proposal_hash

                expected_proposal_hash = canonical_proposal_hash("gmail.create_draft", draft_args)
                token_valid = bool(
                    verify_approval_token_sync(
                        token,
                        "gmail.create_draft",
                        consume=False,
                        expected_run_id=run_id,
                        expected_user_id=user_id if isinstance(user_id, str) else None,
                        expected_proposal_hash=expected_proposal_hash,
                    )
                )

        if not token_valid:
            # Fail-closed: Without valid approval_token, NEVER call draft_creator
            draft_id = None
            content = (
                f"Proposed Draft Follow-up:\n"
                f"Subject: Follow-up: {meeting_ctx.get('title', 'Meeting')}\n"
                f"To: {', '.join(attendees)}\n"
                f"Status: Pending user approval."
            )
            status = "needs_approval"
        elif draft_creator is not None:
            # Token provided AND real tool injected -> delegate draft creation to tool
            try:
                res = draft_creator(
                    {
                        "meeting": meeting_ctx,
                        "messages": messages,
                        "approval_token": token,
                        "user_id": state.get("user_id", ""),
                    }
                )
                draft_id = res.get("draft_id")
                content = res.get("draft_content", "")
                status = res.get("status", "draft_created")
            except Exception as exc:  # noqa: BLE001
                return {
                    "errors": [f"Draft creator failed: {exc}"],
                    "output": {"status": "error", "reason": str(exc), "draft_id": None},
                    "status": "error",
                }
        else:
            # Token provided, but NO draft creator tool injected:
            # Fail-closed: DO NOT fabricate draft_id or pretend execution succeeded
            draft_id = None
            content = (
                f"Subject: Follow-up: {meeting_ctx.get('title', 'Meeting')}\n\n"
                f"To: {', '.join(attendees)}\n\n"
                f"Meeting summary: {meeting_ctx.get('summary', '')}\n"
                f"Referenced {len(messages)} recent correspondence(s)."
            )
            status = RunStatus.APPROVED_UNEXECUTED.value

        output = {
            "draft_id": draft_id,
            "meeting_title": meeting_ctx.get("title"),
            "attendees": attendees,
            "status": status,
        }
        return {
            "draft_id": draft_id,
            "draft_content": content,
            "output": output,
            "status": status,
        }

    builder: StateGraph[WorkflowState, Any, Any, Any] = StateGraph(WorkflowState)
    builder.add_node("fetch_calendar", _fetch_calendar)
    builder.add_node("fetch_emails", _fetch_emails)
    builder.add_node("draft_followup", _draft_followup)

    builder.set_entry_point("fetch_calendar")
    builder.add_edge("fetch_calendar", "fetch_emails")
    builder.add_edge("fetch_emails", "draft_followup")
    builder.add_edge("draft_followup", END)

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
