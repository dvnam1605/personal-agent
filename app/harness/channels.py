"""Graph channel schema and AssistantState boundary conversion (spec P11-00).

``AssistantState`` (``extra="forbid"``, ``validate_assignment=True``) is NEVER
handed to ``StateGraph`` unchanged. These channels are the only state the
graph carries; conversion happens at the harness boundary in both directions.
"""

from __future__ import annotations

import copy
import operator
from typing import Annotated, Any, TypedDict

from app.core.sanitization import strip_sensitive_keys
from app.domain.models.budget import BudgetUsage
from app.domain.models.state import AssistantState
from app.services.approvals import (
    approval_token_bound_tool,
    canonical_proposal_hash,
    verify_approval_token_sync,
)


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
    ctx = sanitized_task.get("context_data") or {}
    token = sanitized_task.get("approval_token") or ctx.get("approval_token")

    # Defense in depth: peek crypto against run/user/tool/proposal (do not consume).
    valid_token: str | None = None
    if token and isinstance(token, str):
        bound_tool = approval_token_bound_tool(token)
        raw_args = ctx.get("arguments") if isinstance(ctx, dict) else None
        if not isinstance(raw_args, dict):
            task_args = sanitized_task.get("arguments")
            raw_args = task_args if isinstance(task_args, dict) else None
        expected_hash = (
            canonical_proposal_hash(bound_tool, raw_args)
            if bound_tool and isinstance(raw_args, dict)
            else None
        )
        if verify_approval_token_sync(
            token,
            tool_name=bound_tool,
            consume=False,
            expected_run_id=state.run_id,
            expected_user_id=state.user_id,
            expected_proposal_hash=expected_hash,
        ):
            valid_token = token

    if not valid_token:
        sanitized_task["permit_mutations"] = False
        sanitized_task["approval_token"] = None
    elif sanitized_task.get("permit_mutations"):
        sanitized_task["approval_token"] = valid_token

    # H2: Strip sensitive keys from context_data so bearer secrets never leak into LLM prompts/logs
    sanitized_task["context_data"] = strip_sensitive_keys(ctx)

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
