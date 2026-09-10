"""Unit tests for Supervisor + Dynamic Multi-Agent DAG (spec P16, MASTER_PLAN §18A, ADR 0011).

Covers all 10 required scenarios:
1. simple request bypasses Supervisor (FastTriage routes to DIRECT_SPECIALIST / STATIC_WORKFLOW)
2. multi-domain open task uses Supervisor (FastTriage routes to SUPERVISOR_DAG, Planner creates valid DAG)
3. independent specialists run in parallel (proven with timing overlap: elapsed < sum(delays))
4. missing data triggers one replan (missing_context detected -> replan node schedules follow-up)
5. replan limit stops safely (bounded replan stops when replan_count reaches max_replans)
6. budget stops runaway planning (DAG validation rejects plans violating max_tasks or max_cost_usd)
7. specialist returns NeedMoreContext -> executor schedules follow-up
8. delegation depth limit prevents unbounded delegation chains (DAG validation rejects depth > max_delegation_depth)
9. continuable subagent session receives follow-up prompt without context loss (ContinuableSessionManager)
10. subagent attempts forbidden tool call outside narrowed scope -> blocked loudly (ScopedToolView)
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

import pytest

from app.agents.declarations import build_first_party_registry
from app.agents.registry import AgentRegistry
from app.domain.enums import (
    ActionClass,
    ActionRiskLevel,
    Domain,
    EvidenceType,
    RouteType,
    TaskStatus,
)
from app.domain.errors import PermissionDeniedError
from app.domain.models import AssistantState, ExecutionBudget
from app.domain.models.evidence import EvidenceItem, EvidenceSource
from app.domain.models.plan import ExecutionPlan, ExecutionTask, TaskDependency
from app.domain.models.supervisor import CapabilityCatalog
from app.domain.models.tool import ToolDefinition, ToolRestriction
from app.harness.supervisor.channels import SupervisorChannels, TaskDispatchChannel
from app.harness.supervisor.graph import SupervisorGraphBuilder
from app.services.supervisor.catalog import build_capability_catalog
from app.services.supervisor.planner import SupervisorPlanner
from app.services.supervisor.session_manager import ContinuableSessionManager
from app.services.supervisor.validator import validate_execution_plan
from app.services.triage import FastTriage
from app.tools.registry import ToolRegistryView


@pytest.fixture
def default_registry() -> AgentRegistry:
    return build_first_party_registry()


@pytest.fixture
def default_catalog(default_registry: AgentRegistry) -> CapabilityCatalog:
    return build_capability_catalog(default_registry)


@pytest.fixture
def base_state() -> AssistantState:
    return AssistantState(
        user_id="user_test_p16",
        request="Test request for supervisor",
        goal="Complete supervisor test suite",
    )


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


# ---------------------------------------------------------------------------
# Scenario 9: Continuable subagent session receives follow-up prompt
# ---------------------------------------------------------------------------
class TestScenario9ContinuableSubagentSession:
    def test_continuable_session_retains_turns_and_context(self) -> None:
        """ContinuableSessionManager preserves history and context across multiple turns without resetting."""
        manager = ContinuableSessionManager()

        session = manager.create_session(
            agent_name="KnowledgeAgent",
            user_id="user_42",
            parent_run_id="run_root_001",
            depth=1,
            context_data={"project_id": "proj_apollo"},
        )
        session_id = session.session_id

        # Turn 1: User asks initial question
        manager.append_message(session_id, "user", "Tìm các tài liệu về dự án Apollo")
        manager.append_message(session_id, "assistant", "Tìm thấy 2 tài liệu: specs.md và plan.md")
        manager.update_context(session_id, {"matched_docs": ["specs.md", "plan.md"]})

        # Turn 2: Follow-up question arrives
        manager.append_message(session_id, "user", "Hãy tóm tắt file plan.md")
        active = manager.require_session(session_id)

        assert active.turn_count == 3
        assert len(active.messages) == 3
        assert active.messages[0].role == "user"
        assert active.messages[0].content == "Tìm các tài liệu về dự án Apollo"
        assert active.messages[2].content == "Hãy tóm tắt file plan.md"
        assert active.context_data["project_id"] == "proj_apollo"
        assert active.context_data["matched_docs"] == ["specs.md", "plan.md"]

        manager.update_context(
            session_id,
            {
                "nested": {"approval_token": "appr_should_not_leak", "note": "ok"},
                "access_token": "secret",
                "accessToken": "camel-secret",
                "apiKey": "k",
            },
        )
        active = manager.require_session(session_id)
        assert "access_token" not in active.context_data
        assert "api_key" not in active.context_data
        assert "approval_token" not in active.context_data.get("nested", {})
        assert active.context_data["nested"]["note"] == "ok"

        leaked = manager.create_session(
            agent_name="KnowledgeAgent",
            user_id="user_42",
            parent_run_id="run_root_001",
            context_data={"refresh_token": "leak", "topic": "ok"},
        )
        assert "refresh_token" not in leaked.context_data
        assert leaked.context_data["topic"] == "ok"
        manager.close_session(leaked.session_id)

        # Close session
        manager.close_session(session_id)
        assert manager.get_session(session_id) is None


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


@pytest.mark.asyncio
async def test_deadline_inside_plan_node_returns_failed(default_catalog: CapabilityCatalog) -> None:
    from app.services.budget_manager import BudgetManager

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


def test_catalog_glob_matches_retrieval_star(default_catalog: CapabilityCatalog) -> None:
    assert default_catalog.get_agent_for_capability("retrieval.*") == "KnowledgeResearchAgent"
    assert (
        default_catalog.get_agent_for_capability("retrieval.retrieve") == "KnowledgeResearchAgent"
    )
    assert default_catalog.get_agent_for_capability("calendar.*") == "CalendarAgent"


@pytest.mark.asyncio
async def test_session_manager_create_and_close_on_execute(
    default_catalog: CapabilityCatalog,
) -> None:
    class CountingSessions(ContinuableSessionManager):
        created = 0
        closed = 0

        def create_session(self, *args: Any, **kwargs: Any) -> Any:
            self.created += 1
            return super().create_session(*args, **kwargs)

        def close_session(self, session_id: str) -> None:
            self.closed += 1
            super().close_session(session_id)

    sessions = CountingSessions()

    async def executor(payload: TaskDispatchChannel) -> dict[str, Any]:
        tid = payload["task_id"]
        return {
            "task_results": {tid: {"status": "completed", "output": "ok"}},
            "completed_task_ids": [tid],
        }

    plan = ExecutionPlan(
        goal="g",
        tasks=[
            ExecutionTask(
                id="t1",
                name="one",
                assigned_agent="CalendarAgent",
                description="one",
                dependencies=[],
            )
        ],
    )
    graph = SupervisorGraphBuilder(
        planner=SupervisorPlanner(),
        catalog=default_catalog,
        task_executor=executor,
        session_manager=sessions,
    ).compile()
    await graph.ainvoke(
        {
            "query": "x",
            "plan": plan,
            "user_id": "user_test_p16",
            "run_id": "run_sess_1",
            "replan_count": 0,
            "completed_task_ids": [],
            "task_results": {},
            "status": "pending",
        }
    )
    assert sessions.created >= 1
    assert sessions.closed >= 1


@pytest.mark.asyncio
async def test_session_reuse_across_multiple_tasks_for_same_agent(
    default_catalog: CapabilityCatalog,
) -> None:
    """Tasks assigned to the same agent within one run reuse the active continuable session."""
    seen_session_ids: list[str | None] = []

    async def executor(payload: TaskDispatchChannel) -> dict[str, Any]:
        tid = payload["task_id"]
        seen_session_ids.append(payload.get("session_id"))
        return {
            "task_results": {tid: {"status": "completed", "output": f"done {tid}"}},
            "completed_task_ids": [tid],
        }

    t1 = ExecutionTask(
        id="t1_cal",
        name="Task 1",
        assigned_agent="CalendarAgent",
        description="First cal task",
        dependencies=[],
    )
    t2 = ExecutionTask(
        id="t2_cal",
        name="Task 2",
        assigned_agent="CalendarAgent",
        description="Second cal task",
        dependencies=[TaskDependency(task_id="t2_cal", depends_on_task_id="t1_cal")],
    )
    plan = ExecutionPlan(tasks=[t1, t2], goal="Sequential calendar tasks")

    session_manager = ContinuableSessionManager()
    builder = SupervisorGraphBuilder(
        planner=SupervisorPlanner(),
        catalog=default_catalog,
        task_executor=executor,
        session_manager=session_manager,
    )
    graph = builder.compile()

    final = await graph.ainvoke(
        {
            "query": "Lịch tuần này và tuần tới",
            "plan": plan,
            "user_id": "user_p16_session",
            "run_id": "run_shared_sess",
            "replan_count": 0,
            "completed_task_ids": [],
            "task_results": {},
            "status": "pending",
        }
    )
    assert final["status"] == "completed"
    assert len(seen_session_ids) == 2
    assert seen_session_ids[0] is not None
    assert seen_session_ids[0] == seen_session_ids[1]


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
