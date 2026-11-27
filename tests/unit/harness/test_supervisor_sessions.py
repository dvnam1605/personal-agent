"""Supervisor DAG continuable subagent sessions (spec P16): follow-up prompts keep their context, and sessions are created, reused and closed around task execution."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.models.supervisor import CapabilityCatalog
from app.domain.models.supervisor.plan import ExecutionPlan, ExecutionTask, TaskDependency
from app.harness.supervisor.channels import TaskDispatchChannel
from app.harness.supervisor.graph import SupervisorGraphBuilder
from app.services.supervisor.planner import SupervisorPlanner
from app.services.supervisor.session_manager import ContinuableSessionManager


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
