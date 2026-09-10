"""Typed state channels and reducers for static compiled StateGraphs (P15/P18A).

Static workflows in P15 run predictable DAGs without invoking the Supervisor
LLM. Parallel branches use LangGraph's ``Send`` primitive; results fan in via
reducer annotations (such as ``operator.add`` on ``branch_results``, ``branch_errors``,
and ``errors``).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

WorkflowStatus = Literal[
    "idle",
    "running",
    "completed",
    "partial_error",
    "failed",
    "needs_approval",
    "approved_unexecuted",
    "calendar_fetch_error",
    "insufficient_calendar_context",
    "searching_parallel",
    "draft_created",
    "no_meeting_found",
    "dossier_synthesized",
    "error",
]


class _RequiredWorkflowChannels(TypedDict):
    """Channels every static workflow activation must supply at entry."""

    workflow_id: str
    run_id: str
    user_id: str
    query: str


class WorkflowState(_RequiredWorkflowChannels, total=False):
    """Execution channels for static workflows with parallel branch reducers."""

    parameters: dict[str, Any]

    # WF-01 Quick Meeting Follow-up channels
    meeting_context: dict[str, Any]
    attendee_messages: list[dict[str, Any]]
    draft_id: str | None
    draft_content: str | None

    # WF-02 Document Search & Briefing channels (parallel Send accumulation)
    pending_domains: list[str]
    branch_results: Annotated[list[dict[str, Any]], operator.add]
    branch_errors: Annotated[list[str], operator.add]
    briefing: str | None
    citations: list[dict[str, Any]]

    # WF-05 Meeting Prep Graph channels
    dossier: dict[str, Any] | None

    # Universal outcome channels
    output: dict[str, Any]
    errors: Annotated[list[str], operator.add]
    status: WorkflowStatus | str
