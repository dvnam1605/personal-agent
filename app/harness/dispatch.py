"""Harness Route Dispatcher and Supervisor DAG bridge (M4 / N2).

Bridges FastTriage RouteDecision objects to the appropriate harness execution engine:
- DIRECT_SPECIALIST -> SpecialistGraphBuilder / SpecialistRunner
- STATIC_WORKFLOW -> Static workflow graph (WF-01 / WF-02)
- SUPERVISOR_DAG -> Phase 16 Supervisor Orchestrator (graceful stub for P15)
- REJECT / CLARIFICATION / CASUAL_RESPONSE -> Immediate terminal outcome
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from app.domain.enums import RouteType
from app.domain.errors import ConfigurationError, ValidationError
from app.domain.models.route import RouteDecision
from app.domain.models.state import AssistantState

logger = structlog.get_logger(__name__)


class HarnessDispatcher:
    """Dispatches triage RouteDecisions to compiled harness graphs or Phase 16 supervisor."""

    def __init__(
        self,
        checkpointer: Any = None,
        *,
        wf01_builder: Callable[..., Any] | None = None,
        wf02_builder: Callable[..., Any] | None = None,
    ) -> None:
        self._checkpointer = checkpointer
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
            res = await graph.ainvoke(init_channels)
            return dict(res)

        if route_type == RouteType.DIRECT_SPECIALIST:
            agent_name = decision.target_agent
            logger.info("dispatching_direct_specialist", agent_name=agent_name, run_id=state.run_id)
            return {
                "status": "dispatched_specialist",
                "target_agent": agent_name,
                "domains": [d.value for d in decision.domains],
                "query": query,
                "parameters": merged_parameters,
            }

        if route_type in (RouteType.SUPERVISOR_DAG, RouteType.SUPERVISOR):
            logger.info(
                "supervisor_dag_dispatch_stub",
                domains=[d.value for d in decision.domains],
                run_id=state.run_id,
            )
            # Phase 16 bridge stub: supervisor planner is introduced in Phase 16
            return {
                "status": "pending_supervisor_orchestration",
                "phase": "P16",
                "route_type": route_type.value,
                "domains": [d.value for d in decision.domains],
                "complexity": decision.complexity.value,
                "reason_code": decision.reason_code,
                "parameters": merged_parameters,
            }

        raise ConfigurationError(f"Unsupported route type for dispatch: {route_type}")
