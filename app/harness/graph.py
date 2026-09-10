"""Minimal specialist execution graph on the LangGraph substrate (P11-00).

Topology is deliberately thin: first-party code (``ModeSelector``,
``SpecialistRunner``) makes every decision; the graph only carries channels,
routes on the selector output, and offers a checkpointer hook for durable
resume (wired in P18). No prebuilt agents, no supervisor helpers, no handoffs.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.registry import AgentRegistry
from app.agents.specialist.react import ModeSelector, SpecialistRunner
from app.domain.enums import ExecutionMode
from app.domain.errors import PermissionDeniedError
from app.domain.models.agent import AgentDefinition
from app.domain.models.specialist import SpecialistTask
from app.domain.models.tool import ToolRestriction
from app.harness.channels import SpecialistChannels
from app.services.capability_gate import CapabilityGate
from app.tools.registry import ToolRegistryView


class SpecialistGraphBuilder:
    """Assemble the select -> execute -> finalize graph around a runner."""

    def __init__(
        self,
        runner: SpecialistRunner,
        agents: AgentRegistry,
        gate: CapabilityGate,
    ) -> None:
        self._runner = runner
        self._agents = agents
        self._gate = gate

    def build(self, *, checkpointer: Any | None = None) -> Any:
        """Compile the graph; pass a saver only when durable resume is wanted."""
        graph: StateGraph[SpecialistChannels, Any, Any, Any] = StateGraph(SpecialistChannels)
        graph.add_node("select_mode", self._select_mode)
        graph.add_node("execute_direct", self._execute_direct)
        graph.add_node("execute_react", self._execute_react)
        graph.add_node("finalize", self._finalize)
        graph.set_entry_point("select_mode")
        graph.add_conditional_edges(
            "select_mode",
            self._route_after_select,
            {"direct": "execute_direct", "react": "execute_react"},
        )
        graph.add_edge("execute_direct", "finalize")
        graph.add_edge("execute_react", "finalize")
        graph.add_edge("finalize", END)
        if checkpointer is not None:
            return graph.compile(checkpointer=checkpointer)
        return graph.compile()

    # ------------------------------------------------------------------
    # Nodes (first-party policy; the graph only carries their outputs)
    # ------------------------------------------------------------------

    def _select_mode(self, state: SpecialistChannels) -> dict[str, Any]:
        task = SpecialistTask.model_validate(state["task_json"])
        agent = self._agents.get(state["agent_name"])
        mode = ModeSelector.select(task, agent)
        return {"mode": mode.value}

    def _route_after_select(self, channels: SpecialistChannels) -> str:
        if channels.get("mode") == ExecutionMode.BOUNDED_REACT.value:
            return "react"
        return "direct"

    async def _execute_direct(self, state: SpecialistChannels) -> dict[str, Any]:
        return await self._execute(state, ExecutionMode.DIRECT)

    async def _execute_react(self, state: SpecialistChannels) -> dict[str, Any]:
        return await self._execute(state, ExecutionMode.BOUNDED_REACT)

    async def _execute(self, channels: SpecialistChannels, mode: ExecutionMode) -> dict[str, Any]:
        task, agent, view = self._resolve_activation(channels, mode)
        outcome = await self._runner.run(
            task, agent, view, run_id=channels["run_id"], user_id=channels["user_id"]
        )
        return {
            "trace_steps": [step.model_dump() for step in outcome.trace.steps],
            "usage": outcome.usage.model_dump(),
            "stop_reason": outcome.trace.stop_reason.value,
            "report_json": outcome.report.model_dump(),
            "escalated_to_react": outcome.trace.escalated_to_react,
            "circuit_broken": outcome.trace.circuit_broken,
        }

    @staticmethod
    def _finalize(state: SpecialistChannels) -> dict[str, Any]:
        if state.get("report_json") is None:
            return {"errors": ["finalize: execute node produced no report"]}
        return {}

    def _resolve_activation(
        self, channels: SpecialistChannels, mode: ExecutionMode
    ) -> tuple[SpecialistTask, AgentDefinition, ToolRegistryView]:
        payload = dict(channels["task_json"])
        payload["mode"] = mode.value
        task = SpecialistTask.model_validate(payload)
        agent = self._agents.get(channels["agent_name"])
        if task.delegation is not None:
            if task.approval_policy != "NEVER" or task.permit_mutations:
                raise PermissionDeniedError(
                    "Delegated specialists cannot carry permit_mutations or a non-NEVER "
                    "approval_policy; mutations are pinned off at DelegationService."
                )
        read_only = (
            (task.delegation is not None and task.delegation.read_only)
            or not task.permit_mutations
            or task.approval_policy == "NEVER"
        )
        view: ToolRegistryView = self._gate.for_agent(agent.name, read_only=read_only)
        allowed_mut = task.context_data.get("allowed_mutation_tool") if task.context_data else None
        if isinstance(allowed_mut, str) and allowed_mut.strip():
            allow = [tool.name for tool in view.list() if not tool.is_mutation] + [
                allowed_mut.strip()
            ]
            view = view.restrict(ToolRestriction(allow=allow))
        if task.sandbox_scope:
            view = view.restrict(ToolRestriction(allow=list(task.sandbox_scope)))
        if task.tool_restriction is not None:
            view = view.restrict(task.tool_restriction)
        return task, agent, view
