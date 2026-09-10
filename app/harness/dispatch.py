"""Harness Route Dispatcher and Supervisor DAG bridge (M4 / N2).

Bridges FastTriage RouteDecision objects to the appropriate harness execution engine:
- DIRECT_SPECIALIST -> SpecialistGraphBuilder / SpecialistRunner
- STATIC_WORKFLOW -> Static workflow graph (WF-01 / WF-02)
- SUPERVISOR_DAG -> Phase 16 Supervisor Orchestrator (fail-closed without a task_executor)
- REJECT / CLARIFICATION / CASUAL_RESPONSE -> Immediate terminal outcome
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

import structlog
from pydantic import ValidationError as PydanticValidationError

from app.core.sanitization import strip_sensitive_keys
from app.domain.enums import RouteType
from app.domain.errors import ConfigurationError, ValidationError
from app.domain.models.budget import BudgetUsage
from app.domain.models.route import RouteDecision
from app.domain.models.state import AssistantState
from app.harness.channels import state_to_channels
from app.services.approvals import (
    approval_token_bound_tool,
    canonical_proposal_hash,
    verify_approval_token_sync,
)

logger = structlog.get_logger(__name__)

GraphBuilder = Callable[..., Any]


class _Unset:
    """Sentinel distinct from ``None`` (explicit fail-closed builders)."""


_UNSET: Final = _Unset()


class HarnessDispatcher:
    """Dispatches triage RouteDecisions to compiled harness graphs or Phase 16 supervisor."""

    def __init__(
        self,
        checkpointer: Any = None,
        *,
        wf01_builder: GraphBuilder | None = None,
        wf02_builder: GraphBuilder | None = None,
        wf05_builder: GraphBuilder | None = None,
        supervisor_builder: GraphBuilder | None | _Unset = _UNSET,
        specialist_builder: GraphBuilder | None | _Unset = _UNSET,
        context_builder: Any | None = None,
    ) -> None:
        if checkpointer is not None:
            self._checkpointer = checkpointer
        else:
            from app.harness.checkpointer import get_default_checkpointer

            self._checkpointer = get_default_checkpointer()
        if context_builder is not None:
            self._context_builder = context_builder
        else:
            from app.harness.runtime import get_default_context_runtime

            self._context_builder = get_default_context_runtime().context_builder
        self._specialist_builder: GraphBuilder | None
        if isinstance(specialist_builder, _Unset):
            from app.harness.runtime import build_default_specialist_graph_builder

            builder = build_default_specialist_graph_builder()
            self._specialist_builder = lambda *, checkpointer=None: builder.build(
                checkpointer=checkpointer
            )
        else:
            self._specialist_builder = specialist_builder
        if wf01_builder is not None:
            self._wf01_builder = wf01_builder
        else:
            from app.harness.workflows.meeting_followup import build_meeting_followup_graph

            self._wf01_builder = build_meeting_followup_graph

        if wf02_builder is not None:
            self._wf02_builder = wf02_builder
        else:
            from app.harness.workflows.document_briefing import build_document_briefing_graph

            self._wf02_builder = build_document_briefing_graph

        if wf05_builder is not None:
            self._wf05_builder = wf05_builder
        else:
            from app.harness.runtime import build_default_meeting_prep_graph_builder

            self._wf05_builder = build_default_meeting_prep_graph_builder

        self._supervisor_builder: GraphBuilder | None
        if isinstance(supervisor_builder, _Unset):

            def _default_supervisor_builder(*, checkpointer: Any = None) -> Any:
                from app.harness.runtime import build_default_supervisor_graph_builder

                return build_default_supervisor_graph_builder().build(checkpointer=checkpointer)

            self._supervisor_builder = _default_supervisor_builder
        else:
            self._supervisor_builder = supervisor_builder

    async def dispatch(
        self,
        decision: RouteDecision,
        state: AssistantState,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch a RouteDecision to the corresponding execution engine.

        Merges ``decision.parameters`` and runtime ``parameters`` so that
        sensitive tokens (like ``approval_token``) or context are never lost.
        """
        route_type = decision.route_type

        if route_type == RouteType.REJECT:
            return {
                "status": "rejected",
                "reason": decision.reasoning,
                "reason_code": decision.reason_code,
            }

        if route_type == RouteType.CLARIFICATION:
            return {
                "status": "clarification_needed",
                "reason": decision.reasoning,
                "reason_code": decision.reason_code,
            }

        if route_type == RouteType.CASUAL_RESPONSE:
            return {
                "status": "casual_response",
                "message": decision.reasoning,
            }

        merged_parameters = {**decision.parameters, **(parameters or {})}
        query = merged_parameters.get("query")
        if not query:
            raise ValidationError(
                "Dispatch requires a non-empty query in decision or parameters.",
                details={"decision": decision.model_dump()},
            )

        if self._context_builder is not None:
            slice_res = await self._context_builder.build_context(query, state.user_id, state)
            if not slice_res.is_empty():
                if slice_res.working_memory:
                    merged_parameters.update(slice_res.working_memory)
                if slice_res.rendered_prompt_section:
                    merged_parameters["context_summary"] = slice_res.rendered_prompt_section

        if route_type in (RouteType.STATIC_WORKFLOW, RouteType.KNOWN_WORKFLOW):
            workflow_id = decision.target_workflow_id
            if not workflow_id:
                raise ConfigurationError(
                    "STATIC_WORKFLOW route requires target_workflow_id.",
                    details={"decision": decision.model_dump()},
                )
            logger.info("dispatching_static_workflow", workflow_id=workflow_id, run_id=state.run_id)

            if workflow_id == "WF-01":
                graph = self._wf01_builder(checkpointer=self._checkpointer)
            elif workflow_id == "WF-02":
                graph = self._wf02_builder(checkpointer=self._checkpointer)
            elif workflow_id == "WF-05":
                graph = self._wf05_builder(checkpointer=self._checkpointer)
            else:
                raise ConfigurationError(
                    f"Unknown static workflow_id '{workflow_id}'.",
                    details={"workflow_id": workflow_id},
                )

            init_channels: dict[str, Any] = {
                "workflow_id": workflow_id,
                "run_id": state.run_id,
                "user_id": state.user_id,
                "query": query,
                "parameters": merged_parameters,
            }
            config = {"configurable": {"thread_id": state.run_id}}
            res = await graph.ainvoke(init_channels, config=config)
            out = dict(res)
            self._record_consolidation_turn(state, query, out)
            return out

        if route_type == RouteType.DIRECT_SPECIALIST:
            agent_name = decision.target_agent
            logger.info("dispatching_direct_specialist", agent_name=agent_name, run_id=state.run_id)

            if self._specialist_builder is None:
                raise ConfigurationError(
                    "DIRECT_SPECIALIST route requires a specialist_builder. "
                    "Inject SpecialistGraphBuilder via HarnessDispatcher(specialist_builder=...). "
                    "Refusing to return a stub specialist result.",
                    details={"agent_name": agent_name, "route_type": route_type.value},
                )
            graph = self._specialist_builder(checkpointer=self._checkpointer)
            token = merged_parameters.get("approval_token")
            permit_mutations = False
            valid_token = None
            bound_tool: str | None = None
            if isinstance(token, str) and token.strip():
                bound_tool = approval_token_bound_tool(token)
                raw_args = merged_parameters.get("arguments")
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
                    permit_mutations = True
                    valid_token = token

            sanitized_context = strip_sensitive_keys(merged_parameters)
            if bound_tool:
                sanitized_context["allowed_mutation_tool"] = bound_tool
            task_json: dict[str, Any] = {
                "agent_name": agent_name or "SpecialistAgent",
                "goal": query,
                "context_data": sanitized_context,
                "permit_mutations": permit_mutations,
                "approval_token": valid_token,
            }
            init_channels = dict(
                state_to_channels(
                    state, agent_name=agent_name or "SpecialistAgent", task_json=task_json
                )
            )
            init_channels["query"] = query
            init_channels["parameters"] = sanitized_context
            config = {"configurable": {"thread_id": state.run_id}}
            res = await graph.ainvoke(init_channels, config=config)
            raw_usage = res.get("usage")
            try:
                usage = BudgetUsage.model_validate(raw_usage if isinstance(raw_usage, dict) else {})
            except PydanticValidationError:
                usage = BudgetUsage()
            state.llm_call_count += usage.llm_calls
            state.tool_call_count += usage.tool_calls
            state.react_steps += usage.react_steps
            out = {
                "status": "completed",
                "target_agent": agent_name,
                "domains": [d.value for d in decision.domains],
                "query": query,
                "parameters": sanitized_context,
                "report": res.get("report_json"),
                "usage": usage.model_dump(),
                "trace_steps": res.get("trace_steps", []),
            }
            self._record_consolidation_turn(state, query, out)
            return out

        if route_type in (RouteType.SUPERVISOR_DAG, RouteType.SUPERVISOR):
            logger.info(
                "dispatching_supervisor_dag",
                domains=[d.value for d in decision.domains],
                run_id=state.run_id,
            )
            if self._supervisor_builder is None:
                raise ConfigurationError(
                    "SUPERVISOR_DAG route requires a supervisor_builder. "
                    "Inject SupervisorGraphBuilder via HarnessDispatcher(supervisor_builder=...). "
                    "Refusing to fabricate a DAG result.",
                    details={"route_type": route_type.value},
                )
            graph = self._supervisor_builder(checkpointer=self._checkpointer)
            init_channels: dict[str, Any] = {
                "query": query,
                "goal": query,
                "user_id": state.user_id,
                "run_id": state.run_id,
                "status": "pending",
                "replan_count": 0,
            }
            config = {"configurable": {"thread_id": state.run_id}}
            res = await graph.ainvoke(init_channels, config=config)
            out = dict(res)
            self._record_consolidation_turn(state, query, out)
            return out

        raise ConfigurationError(f"Unsupported route type for dispatch: {route_type}")

    def _record_consolidation_turn(self, state: AssistantState, query: str, response: Any) -> None:
        """Enqueue completed turn into background consolidation worker."""
        if not state.user_id or not query:
            return
        assistant_resp = ""
        if isinstance(response, dict):
            assistant_resp = str(
                response.get("summary")
                or response.get("report")
                or response.get("final_response")
                or response.get("status")
                or ""
            )
        elif isinstance(response, str):
            assistant_resp = response
        try:
            from app.harness.runtime import get_default_context_runtime

            worker = get_default_context_runtime().consolidation_worker
            worker.enqueue_turn(state.user_id, query, assistant_resp)
        except Exception as exc:  # noqa: BLE001 - memory consolidation must not fail the turn
            logger.warning("consolidation_worker_enqueue_failed", error=str(exc))
