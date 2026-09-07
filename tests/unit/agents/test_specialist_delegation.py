"""Unit tests for the delegation entry point (spec P11-07/08)."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents import AgentRegistry
from app.agents.specialist.delegation import DELEGATION_CONTEXT, DelegationService
from app.agents.specialist.react import SpecialistRunner
from app.domain.errors import ConfigurationError, PermissionDeniedError
from app.domain.models import (
    AgentDefinition,
    DelegationRequest,
    ExecutionBudget,
    SpecialistOutcome,
    SpecialistTask,
    ToolRestriction,
)
from app.services.capability_gate import CapabilityGate
from app.tools.registry import ToolRegistry, ToolRegistryView
from tests.unit.agents import _fakes as fakes
from tests.unit.agents._fakes import DictExecutor, ScriptedChat


class CapturingChild:
    """Child runner double that records the scoped task and view."""

    def __init__(self, outcome: SpecialistOutcome) -> None:
        self._outcome = outcome
        self.tasks: list[SpecialistTask] = []
        self.views: list[ToolRegistryView] = []

    async def run_child(
        self, task: SpecialistTask, agent: object, tools: ToolRegistryView
    ) -> SpecialistOutcome:
        del agent
        self.tasks.append(task)
        self.views.append(tools)
        return self._outcome


def _service(
    child: CapturingChild,
    *,
    agents: list | None = None,
    tools: list | None = None,
) -> DelegationService:
    agent_registry = AgentRegistry(
        agents if agents is not None else [fakes.make_agent("Parent"), fakes.make_agent("Child")]
    )
    tool_registry = ToolRegistry(
        tools
        if tools is not None
        else [fakes.make_read_tool("a.search"), fakes.make_read_tool("b.lookup")]
    )
    return DelegationService(
        agent_registry, tool_registry, CapabilityGate(tool_registry, agent_registry), child
    )


def _request(**overrides: Any) -> DelegationRequest:
    data: dict[str, Any] = {
        "parent_agent": "Parent",
        "parent_depth": 0,
        "target_agent": "Child",
        "goal": "child goal",
    }
    data.update(overrides)
    return DelegationRequest(**data)


def _ok_outcome(depth: int = 1) -> SpecialistOutcome:
    from app.domain.enums import SpecialistStatus, StopReason
    from app.domain.models import SpecialistReport, SpecialistTrace

    return SpecialistOutcome(
        agent_name="Child",
        report=SpecialistReport(status=SpecialistStatus.SUCCESS, summary="child done"),
        trace=SpecialistTrace(stop_reason=StopReason.SUCCESS),
        delegation_depth=depth,
    )


class TestDelegationEnforcement:
    async def test_depth_exceeded_rejects(self) -> None:
        service = _service(CapturingChild(_ok_outcome()))
        request = _request(parent_depth=3)  # child would be depth 4 > limit 3
        with pytest.raises(PermissionDeniedError, match="exceeds limit"):
            await service.delegate(request)

    async def test_parent_without_delegation_permission_rejects(self) -> None:
        agents = [
            fakes.make_agent("Parent", delegation_allowed=False),
            fakes.make_agent("Child"),
        ]
        service = _service(CapturingChild(_ok_outcome()), agents=agents)
        with pytest.raises(PermissionDeniedError, match="not permitted to delegate"):
            await service.delegate(_request())

    async def test_unknown_target_rejects(self) -> None:
        service = _service(CapturingChild(_ok_outcome()))
        with pytest.raises(ConfigurationError, match="unknown agent"):
            await service.delegate(_request(target_agent="Ghost"))

    async def test_target_max_child_depth_narrows_limit(self) -> None:
        agents = [fakes.make_agent("Parent"), fakes.make_agent("Child", max_child_depth=0)]
        service = _service(CapturingChild(_ok_outcome()), agents=agents)
        with pytest.raises(PermissionDeniedError, match="exceeds limit"):
            await service.delegate(_request())


class TestDelegationScope:
    async def test_child_receives_frozen_scope_and_context(self) -> None:
        child = CapturingChild(_ok_outcome())
        service = _service(child)
        result = await service.delegate(_request(parent_depth=1))

        assert len(child.tasks) == 1
        task = child.tasks[0]
        assert task.delegation is not None
        assert task.delegation.delegation_depth == 2
        assert task.delegation.parent_agent == "Parent"
        assert task.delegation.read_only is True
        assert task.permit_mutations is False
        assert DELEGATION_CONTEXT in task.system_preamble
        assert any("depth 2" in line for line in task.system_preamble)
        assert result.delegation_depth == 1
        assert result.success is True
        assert result.output == "child done"

    async def test_tool_restriction_narrows_child_view(self) -> None:
        child = CapturingChild(_ok_outcome())
        service = _service(child)
        await service.delegate(_request(tool_restriction=ToolRestriction(allow=["a.search"])))
        assert child.views[0].tool_names == ("a.search",)

    async def test_child_needs_approval_propagates(self) -> None:
        from app.domain.enums import SpecialistStatus
        from app.domain.models import SpecialistReport

        outcome = _ok_outcome().model_copy(
            update={
                "report": SpecialistReport(
                    status=SpecialistStatus.NEEDS_APPROVAL, summary="need boss"
                ),
                "needs_approval": True,
            }
        )
        service = _service(CapturingChild(outcome))
        result = await service.delegate(_request())
        assert result.needs_approval is True
        assert result.success is False


class TestDelegatedNeverPinEndToEnd:
    async def test_mutation_rejected_without_executing(self) -> None:
        agents = AgentRegistry([fakes.make_agent("Parent"), fakes.make_agent("Child")])
        tools = ToolRegistry([fakes.make_read_tool(), fakes.make_mutation_tool()])
        gate = CapabilityGate(tools, agents)
        chat = ScriptedChat([fakes.calls_turn(("test.write", {"x": 1}))])
        executor = DictExecutor({"test.write": lambda args: fakes.ok_result("test.write")})

        class RunnerChild:
            def __init__(self, runner: SpecialistRunner) -> None:
                self._runner = runner
                self.tasks: list[SpecialistTask] = []

            async def run_child(
                self, task: SpecialistTask, agent: AgentDefinition, tools: ToolRegistryView
            ) -> SpecialistOutcome:
                self.tasks.append(task)
                return await self._runner.run(task, agent, tools, run_id="r1", user_id="u1")

        child = RunnerChild(SpecialistRunner(chat, executor))
        service = DelegationService(agents, tools, gate, child)
        result = await service.delegate(_request(budget=ExecutionBudget()))

        assert result.needs_approval is True
        assert executor.calls == []
        assert child.tasks[0].permit_mutations is False
