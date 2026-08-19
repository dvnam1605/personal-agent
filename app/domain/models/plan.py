"""Execution plan contracts with dependency resolution, owning task validation, and DAG validation."""

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import TaskStatus
from app.domain.models.agent import TaskResult


class TaskDependency(BaseModel):
    """Explicit dependency link between two execution tasks with optional gating condition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(
        ...,
        description="ID of the dependent task.",
    )
    depends_on_task_id: str = Field(
        ...,
        description="ID of the predecessor task that must complete first.",
    )
    condition: str | None = Field(
        default=None,
        description="Optional conditional expression key evaluated in context to trigger task execution.",
    )


class ExecutionTask(BaseModel):
    """An individual unit of work within an execution plan."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique task ID.",
    )
    name: str = Field(
        ...,
        description="Short descriptive name of the task.",
    )
    assigned_agent: str = Field(
        ...,
        description="Specialist agent designated to execute this task.",
    )
    description: str = Field(
        ...,
        description="Detailed instruction and requirements for the task.",
    )
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        description="Current execution status.",
    )
    dependencies: list[TaskDependency] = Field(
        default_factory=list,
        description="List of task dependencies that must complete before this task can start.",
    )
    input_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Input parameters or variables required for task execution.",
    )
    result: TaskResult | None = Field(
        default=None,
        description="Outcome result once task has finished.",
    )

    @field_validator("dependencies", mode="before")
    @classmethod
    def coerce_dependencies(cls, v: Any, info: Any) -> list[Any]:
        """Allow passing string IDs or TaskDependency objects and coerce cleanly."""
        if not isinstance(v, list):
            return v
        coerced: list[Any] = []
        task_id = info.data.get("id", "current_task") if info.data else "current_task"
        for item in v:
            if isinstance(item, str):
                coerced.append(TaskDependency(task_id=task_id, depends_on_task_id=item))
            elif isinstance(item, dict):
                # Ensure task_id defaults to owning task_id if not present
                d = dict(item)
                if "task_id" not in d:
                    d["task_id"] = task_id
                coerced.append(TaskDependency.model_validate(d))
            else:
                coerced.append(item)
        return coerced

    @property
    def dependency_ids(self) -> list[str]:
        """Helper to get list of predecessor task IDs."""
        return [dep.depends_on_task_id for dep in self.dependencies]


class ExecutionPlan(BaseModel):
    """Structured plan composed of tasks with validated DAG dependencies."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    plan_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique execution plan ID.",
    )
    goal: str = Field(
        ...,
        description="High-level user goal that this plan achieves.",
    )
    tasks: list[ExecutionTask] = Field(
        default_factory=list,
        description="List of planned execution tasks.",
    )

    @model_validator(mode="after")
    def validate_task_graph(self) -> "ExecutionPlan":
        """Validate unique task IDs, dependency ownership, existence of referenced dependencies, and absence of cycles."""
        task_ids = {task.id for task in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise ValueError("Duplicate task IDs found in ExecutionPlan.")

        # Ensure task_id in each TaskDependency matches task.id and verifies target existence
        for task in self.tasks:
            for dep in task.dependencies:
                if dep.task_id != task.id:
                    raise ValueError(
                        f"TaskDependency.task_id '{dep.task_id}' does not match owning task id '{task.id}'."
                    )
                if dep.depends_on_task_id not in task_ids:
                    raise ValueError(
                        f"Task '{task.id}' depends on non-existent task '{dep.depends_on_task_id}'."
                    )
                if dep.depends_on_task_id == task.id:
                    raise ValueError(f"Task '{task.id}' cannot depend on itself.")

        # Detect dependency cycles using Kahn's algorithm
        in_degree = {task.id: len(task.dependencies) for task in self.tasks}
        adj: dict[str, list[str]] = {task.id: [] for task in self.tasks}
        for task in self.tasks:
            for dep in task.dependencies:
                adj[dep.depends_on_task_id].append(task.id)

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        visited_count = 0

        while queue:
            current = queue.pop(0)
            visited_count += 1
            for neighbor in adj[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited_count != len(self.tasks):
            raise ValueError("Dependency cycle detected in ExecutionPlan.")

        return self

    def get_ready_tasks(self, context: dict[str, Any] | None = None) -> list[ExecutionTask]:
        """Return tasks that are PENDING and whose dependencies have all COMPLETED with satisfied conditions."""
        completed_task_ids = {t.id for t in self.tasks if t.status == TaskStatus.COMPLETED}
        ready: list[ExecutionTask] = []

        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue

            all_deps_satisfied = True
            for dep in task.dependencies:
                if dep.depends_on_task_id not in completed_task_ids:
                    all_deps_satisfied = False
                    break

                # If dependency specifies a gating condition
                if dep.condition is not None:
                    # Missing or None context cannot satisfy condition
                    if context is None or dep.condition not in context:
                        all_deps_satisfied = False
                        break

                    cond_val = context[dep.condition]
                    if not bool(cond_val):
                        all_deps_satisfied = False
                        break

            if all_deps_satisfied:
                ready.append(task)

        return ready

    def topological_order(self) -> list[ExecutionTask]:
        """Return tasks in a valid topological dependency execution order."""
        task_map = {t.id: t for t in self.tasks}
        in_degree = {t.id: len(t.dependencies) for t in self.tasks}
        adj: dict[str, list[str]] = {t.id: [] for t in self.tasks}
        for t in self.tasks:
            for dep in t.dependencies:
                adj[dep.depends_on_task_id].append(t.id)

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        ordered: list[ExecutionTask] = []

        while queue:
            current_id = queue.pop(0)
            ordered.append(task_map[current_id])
            for neighbor in adj[current_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return ordered
