"""Unit tests for ExecutionPlan and dependency graph validation."""

import pytest
from pydantic import ValidationError

from app.domain.enums import TaskStatus
from app.domain.models import ExecutionPlan, ExecutionTask, TaskDependency


def test_task_dependency_model() -> None:
    """Verify TaskDependency instantiation."""
    dep = TaskDependency(
        task_id="t2",
        depends_on_task_id="t1",
        condition="email_found",
    )
    assert dep.task_id == "t2"
    assert dep.depends_on_task_id == "t1"
    assert dep.condition == "email_found"


def test_valid_execution_plan_and_topological_order() -> None:
    """Verify linear and branching valid DAGs with TaskDependency."""
    t1 = ExecutionTask(
        id="task_1",
        name="Search Email",
        assigned_agent="CommunicationAgent",
        description="Search emails from Nam",
    )
    t2 = ExecutionTask(
        id="task_2",
        name="Fetch Calendar",
        assigned_agent="CalendarAgent",
        description="Find free slots",
        dependencies=[TaskDependency(task_id="task_2", depends_on_task_id="task_1")],
    )
    t3 = ExecutionTask(
        id="task_3",
        name="Synthesize Reply",
        assigned_agent="CommunicationAgent",
        description="Draft response with free slots",
        dependencies=[TaskDependency(task_id="task_3", depends_on_task_id="task_2")],
    )

    plan = ExecutionPlan(
        goal="Schedule follow-up with Nam",
        tasks=[t1, t2, t3],
    )

    assert len(plan.tasks) == 3
    assert t3.dependencies[0].depends_on_task_id == "task_2"

    order = plan.topological_order()
    ordered_ids = [t.id for t in order]
    assert ordered_ids == ["task_1", "task_2", "task_3"]


def test_task_dependency_coercion_from_dict_and_strings() -> None:
    """Verify validation when initializing from dict payloads with string or dict dependencies."""
    task_dict = {
        "id": "task_dyn",
        "name": "Dynamic Task",
        "assigned_agent": "GeneralAgent",
        "description": "Dynamic task description",
        "dependencies": [
            "parent_task_1",
            {"task_id": "task_dyn", "depends_on_task_id": "parent_task_2", "condition": "c_ok"},
        ],
    }
    task = ExecutionTask.model_validate(task_dict)
    assert len(task.dependencies) == 2
    assert task.dependencies[0].depends_on_task_id == "parent_task_1"
    assert task.dependencies[1].depends_on_task_id == "parent_task_2"
    assert task.dependencies[1].condition == "c_ok"
    assert task.dependency_ids == ["parent_task_1", "parent_task_2"]


def test_task_dependency_task_id_mismatch_rejected() -> None:
    """Verify plan rejects TaskDependency where task_id does not match owning task id."""
    t1 = ExecutionTask(
        id="t1",
        name="T1",
        assigned_agent="A1",
        description="Desc 1",
    )
    t2 = ExecutionTask(
        id="t2",
        name="T2",
        assigned_agent="A2",
        description="Desc 2",
        dependencies=[TaskDependency(task_id="wrong_task_id", depends_on_task_id="t1")],
    )
    with pytest.raises(ValidationError, match="does not match owning task id"):
        ExecutionPlan(goal="Mismatch plan", tasks=[t1, t2])


def test_execution_task_assignment_validation() -> None:
    """Verify ExecutionTask validate_assignment rejects invalid statuses."""
    task = ExecutionTask(
        id="t1",
        name="T1",
        assigned_agent="A1",
        description="Desc 1",
    )
    task.status = TaskStatus.RUNNING
    assert task.status == TaskStatus.RUNNING

    with pytest.raises(ValidationError):
        task.status = "invalid_status"  # type: ignore[assignment]


def test_plan_get_ready_tasks_with_conditions() -> None:
    """Verify get_ready_tasks evaluates task completion and dependency conditions."""
    t1 = ExecutionTask(
        id="t1",
        name="T1",
        assigned_agent="A1",
        description="Step 1",
        status=TaskStatus.COMPLETED,
    )
    t2 = ExecutionTask(
        id="t2",
        name="T2",
        assigned_agent="A2",
        description="Step 2",
        dependencies=[
            TaskDependency(
                task_id="t2",
                depends_on_task_id="t1",
                condition="slot_available",
            )
        ],
        status=TaskStatus.PENDING,
    )

    plan = ExecutionPlan(goal="Conditional plan", tasks=[t1, t2])

    # No context provided -> condition cannot be verified -> blocked
    ready_no_context = plan.get_ready_tasks(context=None)
    assert len(ready_no_context) == 0

    # Missing condition key in context -> blocked
    ready_missing_key = plan.get_ready_tasks(context={"unrelated_key": True})
    assert len(ready_missing_key) == 0

    # Condition key is False in context -> blocked
    ready_blocked = plan.get_ready_tasks(context={"slot_available": False})
    assert len(ready_blocked) == 0

    # Condition key is True in context -> ready
    ready_ok = plan.get_ready_tasks(context={"slot_available": True})
    assert len(ready_ok) == 1
    assert ready_ok[0].id == "t2"


def test_plan_duplicate_task_ids_rejected() -> None:
    """Verify plan rejects duplicate task IDs."""
    t1 = ExecutionTask(
        id="dup_id",
        name="T1",
        assigned_agent="A1",
        description="Desc 1",
    )
    t2 = ExecutionTask(
        id="dup_id",
        name="T2",
        assigned_agent="A2",
        description="Desc 2",
    )
    with pytest.raises(ValidationError, match="Duplicate task IDs"):
        ExecutionPlan(goal="Invalid plan", tasks=[t1, t2])


def test_plan_self_dependency_rejected() -> None:
    """Verify plan rejects task depending on itself."""
    t1 = ExecutionTask(
        id="t1",
        name="T1",
        assigned_agent="A1",
        description="Desc 1",
        dependencies=[TaskDependency(task_id="t1", depends_on_task_id="t1")],
    )
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        ExecutionPlan(goal="Self dependency", tasks=[t1])


def test_plan_missing_dependency_rejected() -> None:
    """Verify plan rejects references to non-existent task IDs."""
    t1 = ExecutionTask(
        id="t1",
        name="T1",
        assigned_agent="A1",
        description="Desc 1",
        dependencies=[TaskDependency(task_id="t1", depends_on_task_id="non_existent_task")],
    )
    with pytest.raises(ValidationError, match="depends on non-existent task"):
        ExecutionPlan(goal="Missing dep", tasks=[t1])


def test_plan_cycle_detection() -> None:
    """Verify plan detects and rejects cycles in task dependencies."""
    t1 = ExecutionTask(
        id="t1",
        name="T1",
        assigned_agent="A1",
        description="Desc 1",
        dependencies=[TaskDependency(task_id="t1", depends_on_task_id="t2")],
    )
    t2 = ExecutionTask(
        id="t2",
        name="T2",
        assigned_agent="A2",
        description="Desc 2",
        dependencies=[TaskDependency(task_id="t2", depends_on_task_id="t1")],
    )
    with pytest.raises(ValidationError, match="Dependency cycle detected"):
        ExecutionPlan(goal="Cyclic plan", tasks=[t1, t2])
