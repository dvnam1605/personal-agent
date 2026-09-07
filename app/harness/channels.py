"""Graph channel schema and AssistantState boundary conversion (spec P11-00).

``AssistantState`` (``extra="forbid"``, ``validate_assignment=True``) is NEVER
handed to ``StateGraph`` unchanged. These channels are the only state the
graph carries; conversion happens at the harness boundary in both directions.
"""

from __future__ import annotations

import copy
import operator
from typing import Annotated, Any, TypedDict

from app.domain.models.budget import BudgetUsage
from app.domain.models.state import AssistantState


class _RequiredChannels(TypedDict):
    """Channels every activation must supply at graph entry."""

    run_id: str
    user_id: str
    agent_name: str
    goal: str
    task_json: dict[str, Any]


class SpecialistChannels(_RequiredChannels, total=False):
    """Typed graph state with explicit merge reducers per channel."""

    mode: str | None
    messages: Annotated[list[dict[str, Any]], operator.add]
    trace_steps: Annotated[list[dict[str, Any]], operator.add]
    usage: dict[str, Any]
    stop_reason: str | None
    report_json: dict[str, Any] | None
    errors: Annotated[list[str], operator.add]
    escalated_to_react: bool
    circuit_broken: bool


def state_to_channels(
    state: AssistantState,
    *,
    agent_name: str,
    task_json: dict[str, Any],
) -> SpecialistChannels:
    """Project run state plus activation inputs into initial graph channels.

    The ``permit_mutations`` downgrade here is boundary sanitization only
    (defense in depth): the actual enforcement point is
    ``SpecialistRunner._dispatch_call`` plus the CapabilityGate view built in
    ``SpecialistGraphBuilder._resolve_activation``. Keep the two consistent.
    """
    sanitized_task = copy.deepcopy(task_json)
    if sanitized_task.get("permit_mutations"):
        ctx = sanitized_task.get("context_data") or {}
        if not ctx.get("approval_token") and not sanitized_task.get("approval_token"):
            sanitized_task["permit_mutations"] = False

    return SpecialistChannels(
        run_id=state.run_id,
        user_id=state.user_id,
        agent_name=agent_name,
        goal=state.goal or state.request,
        task_json=sanitized_task,
        mode=None,
        messages=[],
        trace_steps=[],
        usage={},
        stop_reason=None,
        report_json=None,
        errors=[],
        escalated_to_react=False,
        circuit_broken=False,
    )


def apply_channels_to_state(state: AssistantState, channels: SpecialistChannels) -> AssistantState:
    """Fold graph outputs back onto a run state copy (append-only counters).

    The usage channel is validated as a :class:`BudgetUsage` payload instead of
    being read through loose string fallbacks, so schema drift fails loudly at
    the boundary rather than silently contributing zeros to the run counters.
    """
    usage = BudgetUsage.model_validate(channels.get("usage") or {})
    updates: dict[str, Any] = {
        "react_steps": state.react_steps + usage.react_steps,
        "tool_call_count": state.tool_call_count + usage.tool_calls,
        "llm_call_count": state.llm_call_count + usage.llm_calls,
        "prompt_tokens": state.prompt_tokens + usage.prompt_tokens,
        "completion_tokens": state.completion_tokens + usage.completion_tokens,
        "elapsed_seconds": state.elapsed_seconds + usage.elapsed_seconds,
        "supervisor_iterations": state.supervisor_iterations + usage.supervisor_iterations,
        "total_tokens": state.total_tokens + usage.total_tokens,
        "estimated_cost": state.estimated_cost + float(usage.estimated_cost_usd),
        "max_delegation_depth_reached": max(
            state.max_delegation_depth_reached, usage.delegation_depth
        ),
        "errors": [*state.errors, *channels.get("errors", [])],
    }
    return state.model_copy(update=updates)
