"""Supervisor DAG limits (spec P16): missing data triggers one bounded replan, the replan limit and deadlines stop safely, and budget / delegation-depth limits reject runaway plans."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import (
    Domain,
    EvidenceType,
    TaskStatus,
)
from app.domain.models import ExecutionBudget
from app.domain.models.retrieval.evidence import EvidenceItem, EvidenceSource
from app.domain.models.supervisor import CapabilityCatalog
from app.domain.models.supervisor.plan import ExecutionPlan, ExecutionTask, TaskDependency
from app.harness.supervisor.channels import SupervisorChannels, TaskDispatchChannel
from app.harness.supervisor.graph import SupervisorGraphBuilder
from app.services.supervisor.planner import SupervisorPlanner
from app.services.supervisor.validator import validate_execution_plan


# ---------------------------------------------------------------------------
# Scenario 4: Missing data triggers one replan
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestScenario4MissingDataTriggersReplan:
    async def test_missing_data_triggers_bounded_replan_and_schedules_followup(
        self, default_catalog: CapabilityCatalog
    ) -> None:
        """When a specialist reports missing context, evaluate_replan triggers a follow-up task."""
        t1 = ExecutionTask(
            id="t1_cal",
            name="Xem lịch cuộc họp",
            assigned_agent="CalendarAgent",
            description="Họp ngày mai",
            dependencies=[],
        )
        initial_plan = ExecutionPlan(
            tasks=[t1],
            goal="Meeting info",
        )

        planner = SupervisorPlanner()

        async def executor_with_missing_context(payload: TaskDispatchChannel) -> dict[str, Any]:
            tid = payload["task_id"]
            if tid == "t1_cal":
                # First task reports missing attendee email
                return {
                    "task_results": {tid: {"status": TaskStatus.NEEDS_MORE_CONTEXT}},
                    "completed_task_ids": [tid],
                    "missing_context": ["attendee_email"],
                }
            # Follow-up task succeeds with evidence
            ev = EvidenceItem(
                id="ev_contact",
                evidence_type=EvidenceType.CONTACT,
                content="Found email: nam@example.com",
                source=EvidenceSource(
                    source_type="contacts",
                    source_id="c_123",
                    title="Contact Nam",
                ),
            )
            return {
                "task_results": {tid: {"status": "completed", "output": "Found contact"}},
                "completed_task_ids": [tid],
                "evidence": [ev],
            }

        builder = SupervisorGraphBuilder(
            planner=planner,
            catalog=default_catalog,
            task_executor=executor_with_missing_context,
            max_replans=2,
        )
        graph = builder.compile()

        init_channels: SupervisorChannels = {
            "query": "Lấy thông tin người tham gia họp",
            "domains": [Domain.CALENDAR],
            "plan": initial_plan,
            "replan_count": 0,
            "completed_task_ids": [],
            "task_results": {},
            "evidence": [],
            "missing_context": [],
            "needs_approval": [],
            "branch_errors": [],
            "final_synthesis": None,
            "status": "pending",
        }

        final_state = await graph.ainvoke(init_channels)
        assert final_state["replan_count"] == 1
        assert final_state["status"] == "completed"
        assert len(final_state["evidence"]) == 1
        assert final_state["evidence"][0].content == "Found email: nam@example.com"


# ---------------------------------------------------------------------------
# Scenario 5: Replan limit stops safely
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestScenario5ReplanLimitStopsSafely:
    async def test_replan_limit_stops_runaway_loop(
        self, default_catalog: CapabilityCatalog
    ) -> None:
        """When missing context recurs indefinitely, replan stops safely when replan_count >= max_replans."""
        planner = SupervisorPlanner()
        max_replans = 2

        # Specialist always reports missing context
        async def always_missing_executor(payload: TaskDispatchChannel) -> dict[str, Any]:
            tid = payload["task_id"]
            return {
                "task_results": {tid: {"status": TaskStatus.NEEDS_MORE_CONTEXT}},
                "completed_task_ids": [tid],
                "missing_context": ["missing_piece"],
            }

        builder = SupervisorGraphBuilder(
            planner=planner,
            catalog=default_catalog,
            task_executor=always_missing_executor,
            max_replans=max_replans,
        )
        graph = builder.compile()

        init_channels: SupervisorChannels = {
            "query": "Yêu cầu bị thiếu dữ liệu liên tục",
            "domains": [Domain.CALENDAR],
            "plan": None,
            "replan_count": 0,
            "completed_task_ids": [],
            "task_results": {},
            "evidence": [],
            "missing_context": [],
            "needs_approval": [],
            "branch_errors": [],
            "final_synthesis": None,
            "status": "pending",
        }

        final_state = await graph.ainvoke(init_channels)
        # Bounded replan must stop safely at max_replans or on no-progress detection
        assert final_state["replan_count"] <= max_replans
        assert final_state["status"] in ("completed", "no_progress_halted", "needs_more_context")
        assert final_state["final_synthesis"] is not None


# ---------------------------------------------------------------------------
# Scenario 6: Budget stops runaway planning
# ---------------------------------------------------------------------------
class TestScenario6BudgetStopsRunawayPlanning:
    def test_plan_exceeding_max_tasks_rejected_by_validator(
        self, default_catalog: CapabilityCatalog
    ) -> None:
        """A plan with task count exceeding max_tasks ceiling is deterministically rejected."""
        tasks = [
            ExecutionTask(
                id=f"t_{i}",
                name=f"Task {i}",
                assigned_agent="CalendarAgent",
                description=f"Task {i}",
                dependencies=[],
            )
            for i in range(12)
        ]
        plan = ExecutionPlan(tasks=tasks, goal="Exceeds task limit")
        budget = ExecutionBudget()

        res = validate_execution_plan(plan, default_catalog, budget, max_tasks=10)
        assert res.is_valid is False
        assert any("exceeds maximum allowed tasks (10)" in err for err in res.errors)

    def test_plan_exceeding_cost_budget_rejected_by_validator(
        self, default_catalog: CapabilityCatalog
    ) -> None:
        """A plan whose estimated cost exceeds budget.max_cost_usd is rejected."""
        tasks = [
            ExecutionTask(
                id=f"t_{i}",
                name=f"Task {i}",
                assigned_agent="CalendarAgent",
                description=f"Task {i}",
                dependencies=[],
            )
            for i in range(4)
        ]
        plan = ExecutionPlan(tasks=tasks, goal="Exceeds cost limit")
        # 4 tasks * $0.02 = $0.08 > $0.05
        budget = ExecutionBudget(max_cost_usd=Decimal("0.05"))

        res = validate_execution_plan(plan, default_catalog, budget)
        assert res.is_valid is False
        assert any("exceeds budget limit ($0.0500)" in err for err in res.errors)


# ---------------------------------------------------------------------------
# Scenario 8: Delegation depth limit prevents unbounded delegation chains
# ---------------------------------------------------------------------------
class TestScenario8DelegationDepthLimit:
    def test_deep_dependency_chain_exceeding_budget_rejected(
        self, default_catalog: CapabilityCatalog
    ) -> None:
        """A linear dependency chain exceeding budget.max_delegation_depth is rejected."""
        t1 = ExecutionTask(
            id="t1",
            name="Step 1",
            assigned_agent="CalendarAgent",
            description="Step 1",
            dependencies=[],
        )
        t2 = ExecutionTask(
            id="t2",
            name="Step 2",
            assigned_agent="CalendarAgent",
            description="Step 2",
            dependencies=[TaskDependency(task_id="t2", depends_on_task_id="t1")],
        )
        t3 = ExecutionTask(
            id="t3",
            name="Step 3",
            assigned_agent="CalendarAgent",
            description="Step 3",
            dependencies=[TaskDependency(task_id="t3", depends_on_task_id="t2")],
        )
        t4 = ExecutionTask(
            id="t4",
            name="Step 4",
            assigned_agent="CalendarAgent",
            description="Step 4",
            dependencies=[TaskDependency(task_id="t4", depends_on_task_id="t3")],
        )

        plan = ExecutionPlan(tasks=[t1, t2, t3, t4], goal="Deep chain")
        assert plan.calculate_max_depth() == 4

        # max_delegation_depth = 2
        budget = ExecutionBudget(max_delegation_depth=2)
        res = validate_execution_plan(plan, default_catalog, budget)

        assert res.is_valid is False
        assert any(
            "Plan dependency chain depth (4) exceeds maximum delegation depth (2)" in err
            for err in res.errors
        )


@pytest.mark.asyncio
async def test_deadline_inside_plan_node_returns_failed(default_catalog: CapabilityCatalog) -> None:
    from app.services.platform.budget_manager import BudgetManager

    budget = ExecutionBudget(timeout_seconds=0.05)
    manager = BudgetManager(budget=budget, start_time=time.monotonic() - 10.0)
    graph = SupervisorGraphBuilder(
        planner=SupervisorPlanner(),
        catalog=default_catalog,
        budget=budget,
        budget_manager=manager,
        task_executor=None,
    ).compile()
    final = await graph.ainvoke({"query": "Lịch họp và gửi email", "replan_count": 0})
    assert final["status"] == "failed"
    assert final.get("final_synthesis")
    assert final.get("branch_errors")


@pytest.mark.asyncio
async def test_needs_more_context_task_re_executes_after_replan(
    default_catalog: CapabilityCatalog,
) -> None:
    """A task reporting NEEDS_MORE_CONTEXT re-executes and completes after replan."""
    task_executions: list[str] = []

    t_main = ExecutionTask(
        id="task_main",
        name="Main analysis",
        assigned_agent="CalendarAgent",
        description="Analyze schedule with partner email",
        dependencies=[],
    )
    initial_plan = ExecutionPlan(tasks=[t_main], goal="Goal with replan")

    async def dynamic_executor(payload: TaskDispatchChannel) -> dict[str, Any]:
        tid = payload["task_id"]
        task_executions.append(tid)
        if tid == "task_main":
            if "partner_email" in task_executions:
                return {
                    "task_results": {tid: {"status": "completed", "output": "Analysis complete"}},
                    "completed_task_ids": [tid],
                }
            return {
                "task_results": {tid: {"status": TaskStatus.NEEDS_MORE_CONTEXT}},
                "missing_context": ["partner_email"],
            }
        task_executions.append("partner_email")
        ev = EvidenceItem(
            id="ev_email",
            evidence_type=EvidenceType.CONTACT,
            content="partner@corp.com",
            source=EvidenceSource(source_type="crm", source_id="1", title="Partner"),
        )
        return {
            "task_results": {tid: {"status": "completed", "output": "Found partner email"}},
            "completed_task_ids": [tid],
            "evidence": [ev],
        }

    builder = SupervisorGraphBuilder(
        planner=SupervisorPlanner(),
        catalog=default_catalog,
        task_executor=dynamic_executor,
        max_replans=2,
    )
    graph = builder.compile()

    final = await graph.ainvoke(
        {
            "query": "Phân tích lịch",
            "plan": initial_plan,
            "replan_count": 0,
            "completed_task_ids": [],
            "task_results": {},
            "status": "pending",
        }
    )
    assert final["status"] == "completed"
    assert "task_main" in final["completed_task_ids"]
    assert final["task_results"]["task_main"]["status"] == "completed"
    assert task_executions.count("task_main") == 2
