"""Typed state channels for the Supervisor LangGraph DAG substrate (spec P16 / §18A.5).

Follows ADR 0011: typed channels, explicit reducers via operator.add or update_dict,
strict isolation of framework types within app/harness/supervisor/.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, NotRequired, TypedDict

from app.domain.enums import Domain
from app.domain.models.platform.tool import ToolRestriction
from app.domain.models.retrieval.evidence import EvidenceItem
from app.domain.models.supervisor.plan import ExecutionPlan


def update_dict(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge dictionary updates across parallel fan-in steps."""
    merged = dict(existing)
    merged.update(incoming)
    return merged


class TaskDispatchChannel(TypedDict):
    """Payload sent to an individual specialist task runner via LangGraph Send API."""

    task_id: str
    task_name: str
    assigned_agent: str
    description: str
    input_data: dict[str, Any]
    query: str
    user_id: str
    run_id: str
    depth: int
    tool_restriction: ToolRestriction | None
    context_data: dict[str, Any]
    restriction_error: NotRequired[str | None]
    session_id: NotRequired[str | None]


class SupervisorChannels(TypedDict, total=False):
    """Execution channels carrying state across the Supervisor LangGraph DAG."""

    query: str
    domains: list[Domain]
    user_id: str
    run_id: str
    goal: str
    plan: ExecutionPlan | None
    task_results: Annotated[dict[str, Any], update_dict]
    evidence: Annotated[list[EvidenceItem], operator.add]
    completed_task_ids: Annotated[list[str], operator.add]
    branch_errors: Annotated[list[str], operator.add]
    needs_approval: Annotated[list[dict[str, Any]], operator.add]
    missing_context: Annotated[list[str], operator.add]
    handled_missing_context: Annotated[list[str], operator.add]
    handled_missing_task_ids: Annotated[list[str], operator.add]
    replan_count: int
    final_synthesis: str | None
    status: str
