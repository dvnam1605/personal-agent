"""Supervisor DAG routing (spec P16, ADR 0011): simple requests bypass the supervisor, multi-domain tasks get a valid DAG, independent specialists run in parallel, NeedMoreContext yields a follow-up task, and the DAG invariants hold."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.domain.enums import (
    Domain,
    EvidenceType,
    RouteType,
)
from app.domain.models import ExecutionBudget
from app.domain.models.retrieval.evidence import EvidenceItem, EvidenceSource
from app.domain.models.supervisor import CapabilityCatalog
from app.domain.models.supervisor.plan import ExecutionPlan, ExecutionTask, TaskDependency
from app.harness.supervisor.channels import SupervisorChannels, TaskDispatchChannel
from app.harness.supervisor.graph import SupervisorGraphBuilder
from app.services.routing.triage import FastTriage
from app.services.supervisor.planner import SupervisorPlanner
from app.services.supervisor.validator import validate_execution_plan


# ---------------------------------------------------------------------------
# Scenario 1: Simple request bypasses Supervisor
# ---------------------------------------------------------------------------
class TestScenario1SimpleRequestBypassesSupervisor:
    def test_single_domain_calendar_request_bypasses_supervisor(self) -> None:
        """Single-domain calendar query routes to DIRECT_SPECIALIST, not SUPERVISOR_DAG."""
        triage = FastTriage()
        decision = triage.triage("Xem lịch làm việc của tôi ngày mai lúc 9h")

        assert decision.route_type != RouteType.SUPERVISOR_DAG
        assert decision.route_type in (RouteType.DIRECT_SPECIALIST, RouteType.STATIC_WORKFLOW)
        assert Domain.CALENDAR in decision.domains

    def test_single_domain_email_request_bypasses_supervisor(self) -> None:
        """Single-domain email query routes to DIRECT_SPECIALIST or WF-01, not SUPERVISOR."""
        triage = FastTriage()
        decision = triage.triage("Gửi email chào mừng thành viên mới vào team")

        assert decision.route_type != RouteType.SUPERVISOR_DAG
        assert decision.route_type in (RouteType.DIRECT_SPECIALIST, RouteType.STATIC_WORKFLOW)
        assert Domain.COMMUNICATION in decision.domains


# ---------------------------------------------------------------------------
# Scenario 2: Multi-domain open task uses Supervisor
# ---------------------------------------------------------------------------
class TestScenario2MultiDomainOpenTaskUsesSupervisor:
    def test_multi_domain_complex_request_routes_to_supervisor(self) -> None:
        """Multi-domain request routes to SUPERVISOR_DAG and generates a valid ExecutionPlan."""
        triage = FastTriage()
        query = (
            "Tìm tài liệu dự án trong Google Drive, kiểm tra lịch họp tuần tới "
            "rồi gửi email tóm tắt cho toàn đội"
        )
        decision = triage.triage(query)

        assert decision.route_type == RouteType.SUPERVISOR_DAG
        assert len(decision.domains) >= 2

    @pytest.mark.asyncio
    async def test_supervisor_planner_creates_valid_dag_for_multidomain(
        self, default_catalog: CapabilityCatalog
    ) -> None:
        """SupervisorPlanner generates a structured ExecutionPlan with valid capabilities and dependencies."""
        planner = SupervisorPlanner()
        query = "Kiểm tra lịch rảnh và gửi email thông báo họp dự án"
        budget = ExecutionBudget()
        plan = await planner.plan(query, "Lên lịch họp và gửi email", default_catalog, budget)

        assert isinstance(plan, ExecutionPlan)
        assert len(plan.tasks) >= 2
        # Plan validation passes deterministically
        val_res = validate_execution_plan(plan, default_catalog, budget)
        assert val_res.is_valid is True
        assert val_res.errors == []


# ---------------------------------------------------------------------------
# Scenario 3: Independent specialists run in parallel
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestScenario3ParallelSpecialists:
    async def test_independent_specialists_run_concurrently_via_send(
        self, default_catalog: CapabilityCatalog
    ) -> None:
        """Independent tasks (no depends_on) in ready batch MUST execute concurrently via Send API.

        Proven with timing overlap: elapsed wall-clock < sum of individual task delays.
        """
        task_delay = 0.08
        start_times: dict[str, float] = {}
        end_times: dict[str, float] = {}

        # 2 independent tasks
        t1 = ExecutionTask(
            id="task_cal",
            name="Kiểm tra lịch",
            assigned_agent="CalendarAgent",
            description="Kiểm tra lịch ngày mai",
            dependencies=[],
        )
        t2 = ExecutionTask(
            id="task_drive",
            name="Tìm tài liệu",
            assigned_agent="KnowledgeResearchAgent",
            description="Tìm kiếm tài liệu dự án",
            dependencies=[],
        )

        plan = ExecutionPlan(
            tasks=[t1, t2],
            goal="Test parallel execution",
        )

        class StaticPlanPlanner(SupervisorPlanner):
            async def plan(self, *a: Any, **kw: Any) -> ExecutionPlan:
                return plan

        async def timed_executor(payload: TaskDispatchChannel) -> dict[str, Any]:
            tid = payload["task_id"]
            start_times[tid] = time.perf_counter()
            await asyncio.sleep(task_delay)
            end_times[tid] = time.perf_counter()
            ev = EvidenceItem(
                id=f"ev_{tid}",
                evidence_type=EvidenceType.SYSTEM_FACT,
                content=f"Fact from {tid}",
                source=EvidenceSource(
                    source_type="test_run",
                    source_id=tid,
                    title=f"Result for {tid}",
                ),
            )
            return {
                "task_results": {tid: {"status": "completed", "output": f"Done {tid}"}},
                "completed_task_ids": [tid],
                "evidence": [ev],
            }

        builder = SupervisorGraphBuilder(
            planner=StaticPlanPlanner(),
            catalog=default_catalog,
            task_executor=timed_executor,
        )
        graph = builder.compile()

        init_channels: SupervisorChannels = {
            "query": "Chạy song song",
            "domains": [Domain.CALENDAR, Domain.KNOWLEDGE_RESEARCH],
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

        wall_start = time.perf_counter()
        final_state = await graph.ainvoke(init_channels)
        wall_elapsed = time.perf_counter() - wall_start

        # Both tasks completed
        assert "task_cal" in final_state["completed_task_ids"]
        assert "task_drive" in final_state["completed_task_ids"]
        assert final_state["status"] == "completed"

        # Concurrency proof:
        # 1. Both tasks started before either finished (temporal overlap)
        assert start_times["task_drive"] < end_times["task_cal"]
        assert start_times["task_cal"] < end_times["task_drive"]
        # 2. Total wall-clock time is significantly less than 2 * task_delay
        assert wall_elapsed < (task_delay * 1.8), (
            f"Sequential execution detected: elapsed {wall_elapsed:.3f}s >= {task_delay * 1.8:.3f}s"
        )


# ---------------------------------------------------------------------------
# Scenario 7: Specialist returns NeedMoreContext -> executor schedules follow-up
# ---------------------------------------------------------------------------
class TestScenario7NeedMoreContextSignalHandling:
    @pytest.mark.asyncio
    async def test_planner_creates_follow_up_task_for_need_more_context(
        self, default_catalog: CapabilityCatalog
    ) -> None:
        """Planner creates a targeted follow-up task resolving NeedMoreContext."""
        planner = SupervisorPlanner()
        original_task = ExecutionTask(
            id="t_original",
            name="Soạn thư cảm ơn",
            assigned_agent="CommunicationAgent",
            description="Soạn thư cảm ơn khách hàng",
            dependencies=[],
        )
        plan = ExecutionPlan(tasks=[original_task], goal="Send email")
        missing = ["attendee_email"]
        budget = ExecutionBudget()

        new_plan = await planner.replan(
            original_plan=plan,
            completed_tasks=[original_task],
            missing_context=missing,
            catalog=default_catalog,
            budget=budget,
        )

        assert len(new_plan.tasks) == 2
        followup_task = new_plan.tasks[1]
        assert followup_task.id.startswith("replan_")
        assert "attendee_email" in followup_task.description


# ---------------------------------------------------------------------------
# Invariant tests: Forbidden constructs & DAG Cycle detection
# ---------------------------------------------------------------------------
class TestSupervisorInvariants:
    def test_dag_cycle_detection_rejects_circular_dependencies(self) -> None:
        """Plan with cyclic dependency is deterministically rejected at validation."""
        t1 = ExecutionTask(
            id="task_a",
            name="A",
            assigned_agent="CalendarAgent",
            description="A",
            dependencies=[TaskDependency(task_id="task_a", depends_on_task_id="task_b")],
        )
        t2 = ExecutionTask(
            id="task_b",
            name="B",
            assigned_agent="CalendarAgent",
            description="B",
            dependencies=[TaskDependency(task_id="task_b", depends_on_task_id="task_a")],
        )
        # ExecutionPlan validation catches dependency cycle via Kahn's algorithm
        with pytest.raises(ValueError, match="Dependency cycle detected"):
            ExecutionPlan(tasks=[t1, t2], goal="Cycle plan")

    def test_forbidden_constructs_not_imported_in_app(self) -> None:
        """Enforces P16-00: langgraph_supervisor and create_handoff_tool must never be used in app."""
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        app_dir = repo_root / "app"

        offenders: list[str] = []
        for py_path in app_dir.rglob("*.py"):
            source = py_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "langgraph_supervisor" in alias.name or "handoff" in alias.name:
                            offenders.append(f"{py_path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if "langgraph_supervisor" in mod or "handoff" in mod:
                        offenders.append(f"{py_path.name}: from {mod}")
                    for alias in node.names:
                        if alias.name in ("create_supervisor", "create_handoff_tool"):
                            offenders.append(f"{py_path.name}: import {alias.name}")

        assert offenders == [], f"Forbidden constructs found in codebase: {offenders}"


def test_catalog_glob_matches_retrieval_star(default_catalog: CapabilityCatalog) -> None:
    assert default_catalog.get_agent_for_capability("retrieval.*") == "KnowledgeResearchAgent"
    assert (
        default_catalog.get_agent_for_capability("retrieval.retrieve") == "KnowledgeResearchAgent"
    )
    assert default_catalog.get_agent_for_capability("calendar.*") == "CalendarAgent"
