"""Execution plan contracts with dependency resolution, owning task validation, and DAG validation."""

import uuid
from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import Domain, TaskStatus
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
        description=(
            "Optional condition key evaluated against execution context. "
            "Can be a predecessor task ID (checked for non-failure status in task_results) "
            "or an evaluation key present in context. Note that Supervisor DAG passes task_results as context."
        ),
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

    def check_graph_invariants(self) -> None:
        """Validate unique task IDs, dependency ownership, existence of referenced dependencies, and absence of cycles."""
        task_ids = {task.id for task in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise ValueError("Duplicate task IDs found in ExecutionPlan.")

        # Ensure task_id in each TaskDependency matches task.id and verifies target existence
        for task in self.tasks:
            for dep in task.dependencies:
                dep_task_id = dep.task_id
                dep_target = dep.depends_on_task_id
                if dep_task_id != task.id:
                    raise ValueError(
                        f"TaskDependency.task_id '{dep_task_id}' does not match owning task id '{task.id}'."
                    )
                if dep_target not in task_ids:
                    raise ValueError(
                        f"Task '{task.id}' depends on non-existent task '{dep_target}'."
                    )
                if dep_target == task.id:
                    raise ValueError(f"Task '{task.id}' cannot depend on itself.")

        self._kahn_order()

    def _kahn_order(self) -> list[str]:
        """Return task ids in topological order or raise on a cycle."""
        in_degree = {task.id: len(task.dependencies) for task in self.tasks}
        adj: dict[str, list[str]] = {task.id: [] for task in self.tasks}
        for task in self.tasks:
            for dep in task.dependencies:
                adj[dep.depends_on_task_id].append(task.id)

        queue: deque[str] = deque(tid for tid, deg in in_degree.items() if deg == 0)
        ordered: list[str] = []
        while queue:
            current = queue.popleft()
            ordered.append(current)
            for neighbor in adj[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(ordered) != len(self.tasks):
            raise ValueError("Dependency cycle detected in ExecutionPlan.")
        return ordered

    @model_validator(mode="after")
    def validate_task_graph(self) -> "ExecutionPlan":
        """Run graph invariant validations upon initialization."""
        self.check_graph_invariants()
        return self

    def get_ready_tasks(
        self,
        context: dict[str, Any] | None = None,
        *,
        completed_ids: set[str] | None = None,
    ) -> list[ExecutionTask]:
        """Return tasks whose dependencies are satisfied.

        *completed_ids* lets callers avoid mutating shared ``task.status``
        during parallel Send dispatch. When omitted, completion is read from
        ``task.status == COMPLETED``.
        """
        completed_task_ids = (
            completed_ids
            if completed_ids is not None
            else {t.id for t in self.tasks if t.status == TaskStatus.COMPLETED}
        )
        ready: list[ExecutionTask] = []

        for task in self.tasks:
            if task.id in completed_task_ids:
                continue
            if completed_ids is None and task.status != TaskStatus.PENDING:
                continue

            all_deps_satisfied = True
            for dep in task.dependencies:
                if dep.depends_on_task_id not in completed_task_ids:
                    all_deps_satisfied = False
                    break

                if dep.condition is not None:
                    if context is None or dep.condition not in context:
                        all_deps_satisfied = False
                        break

                    cond_val = context[dep.condition]
                    if isinstance(cond_val, dict):
                        status = str(cond_val.get("status") or "").lower()
                        if status in ("failed", "error", "blocked", "timed_out"):
                            all_deps_satisfied = False
                            break
                    elif not bool(cond_val):
                        all_deps_satisfied = False
                        break

            if all_deps_satisfied:
                ready.append(task)

        return ready

    def topological_order(self) -> list[ExecutionTask]:
        """Return tasks in a valid topological dependency execution order."""
        task_map = {t.id: t for t in self.tasks}
        return [task_map[tid] for tid in self._kahn_order()]

    def calculate_task_depths(self) -> dict[str, int]:
        """Calculate the 1-indexed dependency depth for every task in the plan."""
        if not self.tasks:
            return {}
        depths: dict[str, int] = {}
        for task in self.topological_order():
            dep_depths = [depths.get(dep.depends_on_task_id, 0) for dep in task.dependencies]
            depths[task.id] = 1 + max(dep_depths, default=0)
        return depths

    def calculate_max_depth(self) -> int:
        """Calculate the maximum dependency depth across all task chains."""
        depths = self.calculate_task_depths()
        return max(depths.values(), default=0)


class CapabilityRequest(BaseModel):
    """Structured signal emitted when a specialist needs cross-domain assistance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_domain: Domain = Field(
        ...,
        description="Domain of the capability needed to proceed.",
    )
    reason: str = Field(
        ...,
        min_length=1,
        description="Justification for why external domain capability is required.",
    )
    query_hint: str = Field(
        ...,
        min_length=1,
        description="Search query or instruction hint for the requested capability.",
    )


class NeedMoreContext(BaseModel):
    """Signal when a task cannot proceed due to missing factual or domain information."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: str = Field(
        ...,
        min_length=1,
        description="Why the current context is insufficient.",
    )
    what_is_needed: str = Field(
        ...,
        min_length=1,
        description="Precise description of information or document needed.",
    )
    target_capability: str | None = Field(
        default=None,
        description="Suggested capability name to obtain the missing information.",
    )


class PlanValidationResult(BaseModel):
    """Outcome of deterministic DAG validation for an ExecutionPlan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    is_valid: bool = Field(
        ...,
        description="Whether the execution plan satisfies all structural, capability, and budget constraints.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="List of validation errors found if is_valid is False.",
    )
    max_depth: int = Field(
        default=0,
        ge=0,
        description="Maximum dependency chain depth in the task DAG.",
    )
    task_count: int = Field(
        default=0,
        ge=0,
        description="Total number of tasks in the plan.",
    )
    estimated_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Estimated token cost or budget impact.",
    )
