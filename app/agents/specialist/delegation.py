"""Single delegation entry point (spec P11-07/08, MASTER_PLAN §8.6).

Neither Supervisor nor Workflow Executor may bypass
:meth:`DelegationService.delegate`. The child runs with a frozen tool scope, an
injected ``DELEGATION_CONTEXT`` system message, and approvals pinned off
(``approval_policy = NEVER``): mutation attempts are rejected automatically
and surface as ``needs_approval`` instead of executing.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.agents.registry import AgentRegistry
from app.domain.errors import ConfigurationError, NotFoundError, PermissionDeniedError
from app.domain.models.agent import AgentDefinition, DelegationContext, DelegationResult
from app.domain.models.specialist import (
    DelegationRequest,
    SpecialistOutcome,
    SpecialistTask,
)
from app.domain.models.tool import ToolRestriction
from app.services.capability_gate import CapabilityGate
from app.tools.registry import ToolRegistry, ToolRegistryView

logger = logging.getLogger(__name__)

DELEGATION_CONTEXT = (
    "You are a delegated specialist: your permission scope was fixed "
    "when you were started and cannot be widened from inside this "
    "execution. Operations that require approval are rejected "
    "automatically. When the task needs access beyond that scope, "
    "return a CapabilityRequest so the orchestration layer can handle it."
)


@runtime_checkable
class ChildRunner(Protocol):
    """Executes one scoped child activation (wired to SpecialistRunner later)."""

    async def run_child(
        self,
        task: SpecialistTask,
        agent: AgentDefinition,
        tools: ToolRegistryView,
    ) -> SpecialistOutcome: ...


class DelegationService:
    """Enforce depth, scope, context, and approval pinning for delegation."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        tool_registry: ToolRegistry,
        gate: CapabilityGate,
        child_runner: ChildRunner,
    ) -> None:
        self._agents = agent_registry
        self._tools = tool_registry
        self._gate = gate
        self._child_runner = child_runner

    def build_child_task(
        self, request: DelegationRequest, *, view: ToolRegistryView | None = None
    ) -> SpecialistTask:
        """Resolve depth/scope/context without executing (pure, testable)."""
        parent = self._resolve_agent(request.parent_agent)
        target = self._resolve_agent(request.target_agent)
        if not parent.delegation_allowed:
            raise PermissionDeniedError(
                f"Agent '{parent.name}' is not permitted to delegate.",
                details={"agent_name": parent.name},
            )
        child_depth = request.parent_depth + 1
        limit = self._depth_limit(request, target)
        if child_depth > limit:
            raise PermissionDeniedError(
                f"Delegation depth {child_depth} exceeds limit {limit}.",
                details={"depth": child_depth, "limit": limit},
            )
        scoped = view if view is not None else self._scoped_view(request, target)
        delegation = DelegationContext(
            parent_agent=parent.name,
            target_agent=target.name,
            delegation_depth=child_depth,
            allowed_tools=sorted(scoped.tool_names),
            # Truthful metadata: permit_mutations=False below rejects every
            # mutation, so the child is strictly read-only in effect.
            read_only=True,
        )
        scope_line = (
            f"Delegated by '{parent.name}' at depth {child_depth} "
            f"(limit {limit}). Permitted tools: "
            f"{', '.join(delegation.allowed_tools) or '(none)'}."
        )
        return SpecialistTask(
            agent_name=target.name,
            goal=request.goal,
            mode=None,
            context_data=dict(request.context_data),
            budget=request.budget,
            tool_restriction=request.tool_restriction,
            permit_mutations=False,
            delegation=delegation,
            system_preamble=(*request.system_preamble, DELEGATION_CONTEXT, scope_line),
        )

    async def delegate(self, request: DelegationRequest) -> DelegationResult:
        """Full entry point: resolve scope, execute the child, collect the result."""
        target = self._resolve_agent(request.target_agent)
        view = self._scoped_view(request, target)
        task = self.build_child_task(request, view=view)
        outcome = await self._child_runner.run_child(task, target, view)
        logger.info(
            "delegation_done",
            extra={
                "parent": request.parent_agent,
                "target": request.target_agent,
                "depth": task.delegation.delegation_depth if task.delegation else 0,
                "status": outcome.report.status.value,
            },
        )
        return DelegationResult(
            agent_name=target.name,
            success=outcome.report.status.value == "success",
            output=outcome.report.summary,
            needs_approval=outcome.needs_approval,
            delegation_depth=outcome.delegation_depth,
        )

    # ------------------------------------------------------------------

    def _resolve_agent(self, name: str) -> AgentDefinition:
        try:
            return self._agents.get(name)
        except NotFoundError as exc:
            raise ConfigurationError(
                f"Delegation references unknown agent '{name}'.",
                details={"agent_name": name},
            ) from exc

    def _depth_limit(self, request: DelegationRequest, target: AgentDefinition) -> int:
        limits = [request.budget.max_delegation_depth]
        if target.max_child_depth is not None:
            limits.append(target.max_child_depth)
        return min(limits)

    def _scoped_view(self, request: DelegationRequest, target: AgentDefinition) -> ToolRegistryView:
        view: ToolRegistryView = self._gate.for_agent(target.name)
        restriction: ToolRestriction | None = request.tool_restriction
        if restriction is not None:
            view = view.restrict(restriction)
        return view
