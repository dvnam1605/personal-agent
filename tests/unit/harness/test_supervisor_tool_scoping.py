"""Supervisor DAG tool scoping (spec P16): a subagent calling a tool outside its narrowed scope is blocked loudly, and an invalid restriction fails the task instead of running it unrestricted."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.enums import (
    ActionClass,
    ActionRiskLevel,
)
from app.domain.errors import PermissionDeniedError
from app.domain.models.platform.tool import ToolDefinition, ToolRestriction
from app.domain.models.supervisor import CapabilityCatalog
from app.domain.models.supervisor.plan import ExecutionPlan, ExecutionTask
from app.harness.supervisor.channels import TaskDispatchChannel
from app.harness.supervisor.graph import SupervisorGraphBuilder
from app.services.supervisor.planner import SupervisorPlanner
from app.tools.registry import ToolRegistryView


# ---------------------------------------------------------------------------
# Scenario 10: Subagent attempts forbidden tool call outside narrowed scope
# ---------------------------------------------------------------------------
class TestScenario10ToolScopingFilterBlockedLoudly:
    def test_subagent_tool_outside_restriction_blocked_loudly(self) -> None:
        """A subagent restricted to read-only tool is blocked loudly if attempting to access unallowed tool."""
        read_tool = ToolDefinition(
            name="calendar.query",
            description="Query calendar",
            category="calendar",
            capabilities=["calendar_query"],
            action_class=ActionClass.READ,
            is_mutation=False,
        )
        write_tool = ToolDefinition(
            name="calendar.delete",
            description="Delete calendar event",
            category="calendar",
            capabilities=["calendar_delete"],
            action_class=ActionClass.DESTRUCTIVE,
            risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
            is_mutation=True,
        )

        view = ToolRegistryView([read_tool, write_tool], is_read_only=False)

        # Narrow scope with ToolRestriction allowing only calendar.query
        restriction = ToolRestriction(allow=["calendar.query"])
        scoped_view = view.restrict(restriction)

        # Allowed tool works
        assert scoped_view.get("calendar.query").name == "calendar.query"

        # Forbidden tool fails loudly with PermissionDeniedError
        with pytest.raises(PermissionDeniedError) as exc_info:
            scoped_view.get("calendar.delete", agent_name="CalendarSubagent")

        assert "calendar.delete" in str(exc_info.value)
        assert "restricted for agent 'CalendarSubagent'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_restriction_fails_task_not_unrestricted(
    default_catalog: CapabilityCatalog,
) -> None:
    called: list[str] = []

    async def executor(payload: TaskDispatchChannel) -> dict[str, Any]:
        called.append(payload["task_id"])
        tid = payload["task_id"]
        return {
            "task_results": {tid: {"status": "completed", "output": "should-not-run"}},
            "completed_task_ids": [tid],
        }

    plan = ExecutionPlan(
        goal="restricted",
        tasks=[
            ExecutionTask(
                id="t_bad",
                name="bad restriction",
                assigned_agent="CalendarAgent",
                description="x",
                input_data={"allowed_tools": "not-a-list"},
                dependencies=[],
            )
        ],
    )
    graph = SupervisorGraphBuilder(
        planner=SupervisorPlanner(),
        catalog=default_catalog,
        task_executor=executor,
    ).compile()
    final = await graph.ainvoke(
        {
            "query": "x",
            "plan": plan,
            "replan_count": 0,
            "completed_task_ids": [],
            "task_results": {},
            "branch_errors": [],
            "status": "pending",
        }
    )
    assert called == []
    assert final["task_results"]["t_bad"]["status"] == "failed"
    assert final["status"] in ("failed", "partial_failure")
