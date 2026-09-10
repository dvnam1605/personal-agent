"""Deterministic DAG validation for Supervisor execution plans (spec P16-03).

Validates:
1. Capability & assigned agent validity against CapabilityCatalog.
2. Dependency existence, validity, and absence of cycles.
3. Task ceiling limits.
4. Delegation depth bounds per dependency chain against ExecutionBudget.
5. Estimated budget / token impact.
"""

from __future__ import annotations

from app.core.config import settings as app_settings
from app.domain.models.platform.budget import ExecutionBudget
from app.domain.models.supervisor import CapabilityCatalog
from app.domain.models.supervisor.plan import ExecutionPlan, PlanValidationResult

DEFAULT_MAX_TASKS = 10


def _cost_per_task_usd() -> float:
    return float(app_settings.supervisor_budget.cost_per_task_usd)


def validate_execution_plan(
    plan: ExecutionPlan,
    catalog: CapabilityCatalog,
    budget: ExecutionBudget,
    *,
    max_tasks: int = DEFAULT_MAX_TASKS,
) -> PlanValidationResult:
    """Validate an ExecutionPlan deterministically before dispatching.

    Enforces P16-03: Rejects invalid capabilities, cycles, depth overflows,
    and task ceiling breaches prior to execution.
    """
    errors: list[str] = []

    if not plan.tasks:
        errors.append("Execution plan contains no tasks.")
        return PlanValidationResult(
            is_valid=False,
            errors=errors,
            max_depth=0,
            task_count=0,
            estimated_cost=0.0,
        )

    # 1. Validate assigned agent exists in CapabilityCatalog
    valid_agent_names = {ag.agent_name for ag in catalog.agents}
    for task in plan.tasks:
        if task.assigned_agent not in valid_agent_names:
            # Check if assigned_agent is a capability name mapped to an agent
            mapped = catalog.get_agent_for_capability(task.assigned_agent)
            if mapped is None:
                errors.append(
                    f"Task '{task.id}' ({task.name}) assigns unknown agent/capability '{task.assigned_agent}'."
                )

    # 2. Validate structural integrity & cycles
    try:
        plan.check_graph_invariants()
    except ValueError as exc:
        errors.append(f"DAG structure error: {exc}")

    # 3. Validate task ceiling
    task_count = len(plan.tasks)
    if task_count > max_tasks:
        errors.append(
            f"Plan task count ({task_count}) exceeds maximum allowed tasks ({max_tasks})."
        )

    # 4. Validate delegation depth against budget
    max_depth = plan.calculate_max_depth()
    if max_depth > budget.max_delegation_depth:
        errors.append(
            f"Plan dependency chain depth ({max_depth}) exceeds maximum delegation depth ({budget.max_delegation_depth})."
        )

    # 5. Estimate cost
    estimated_cost = round(task_count * _cost_per_task_usd(), 4)
    if budget.max_cost_usd is not None and estimated_cost > float(budget.max_cost_usd):
        errors.append(
            f"Estimated plan cost (${estimated_cost:.4f}) exceeds budget limit (${float(budget.max_cost_usd):.4f})."
        )

    is_valid = len(errors) == 0
    return PlanValidationResult(
        is_valid=is_valid,
        errors=errors,
        max_depth=max_depth,
        task_count=task_count,
        estimated_cost=estimated_cost,
    )
