"""Unit tests for ExecutionBudget, BudgetUsage assignment safety, and budget tracking."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.models import (
    BudgetUsage,
    ExecutionBudget,
    evaluate_budget_violations,
)


def test_budget_defaults_and_validation() -> None:
    """Verify default limits and non-negative constraints."""
    budget = ExecutionBudget()
    assert budget.max_llm_calls == 5
    assert budget.max_tool_calls == 10
    assert budget.max_react_steps == 5
    assert budget.max_supervisor_iterations == 3
    assert budget.timeout_seconds == 30.0
    assert budget.max_total_tokens == 16000

    with pytest.raises(ValidationError):
        ExecutionBudget(max_llm_calls=-1)

    with pytest.raises(ValidationError):
        ExecutionBudget(timeout_seconds=0.0)


def test_budget_usage_recording() -> None:
    """Verify usage counters increment correctly."""
    usage = BudgetUsage()
    usage.record_llm_call(2)
    usage.record_tool_call(3)
    usage.record_react_step(1)
    usage.record_supervisor_iteration(1)
    usage.record_llm_metrics(prompt_tokens=12, completion_tokens=8, cost_usd=Decimal("0.000123"))
    usage.elapsed_seconds = 12.5

    assert usage.llm_calls == 2
    assert usage.tool_calls == 3
    assert usage.react_steps == 1
    assert usage.supervisor_iterations == 1
    assert usage.elapsed_seconds == 12.5
    assert usage.total_tokens == 20
    assert usage.estimated_cost_usd == Decimal("0.000123")


def test_budget_usage_negative_or_zero_increment_rejected() -> None:
    """Verify BudgetUsage rejects negative or zero increments."""
    usage = BudgetUsage()
    with pytest.raises(ValueError, match="Increment count must be positive"):
        usage.record_llm_call(0)

    with pytest.raises(ValueError, match="Increment count must be positive"):
        usage.record_llm_call(-1)

    with pytest.raises(ValueError, match="Increment count must be positive"):
        usage.record_tool_call(-5)

    with pytest.raises(ValueError, match="Increment count must be positive"):
        usage.record_react_step(0)

    with pytest.raises(ValueError, match="Increment count must be positive"):
        usage.record_supervisor_iteration(-2)


def test_budget_usage_assignment_validation() -> None:
    """Verify BudgetUsage validate_assignment rejects direct assignment of negative values."""
    usage = BudgetUsage()
    usage.llm_calls = 5
    assert usage.llm_calls == 5

    with pytest.raises(ValidationError):
        usage.llm_calls = -1

    with pytest.raises(ValidationError):
        usage.tool_calls = -3

    with pytest.raises(ValidationError):
        usage.elapsed_seconds = -1.0


def test_evaluate_budget_violations_clean() -> None:
    """Verify no violations when usage is strictly within limits."""
    budget = ExecutionBudget(
        max_llm_calls=5,
        max_tool_calls=10,
        max_react_steps=5,
        max_supervisor_iterations=3,
        timeout_seconds=30.0,
    )
    usage = BudgetUsage(
        llm_calls=5,
        tool_calls=10,
        react_steps=5,
        supervisor_iterations=3,
        elapsed_seconds=30.0,
    )
    violations = evaluate_budget_violations(budget, usage)
    assert len(violations) == 0


def test_evaluate_budget_violations_breached() -> None:
    """Verify violations trigger when any limit is exceeded."""
    budget = ExecutionBudget(
        max_llm_calls=2,
        max_tool_calls=3,
        max_react_steps=2,
        max_supervisor_iterations=1,
        timeout_seconds=10.0,
    )
    usage = BudgetUsage(
        llm_calls=3,
        tool_calls=4,
        react_steps=3,
        supervisor_iterations=2,
        elapsed_seconds=15.2,
    )
    violations = evaluate_budget_violations(budget, usage)
    assert len(violations) == 5

    res_types = {v.resource_type for v in violations}
    assert res_types == {
        "llm_calls",
        "tool_calls",
        "react_steps",
        "supervisor_iterations",
        "timeout",
    }


def test_evaluate_budget_token_and_cost_violations() -> None:
    """Verify token and USD ceilings are first-class budget violations."""
    budget = ExecutionBudget(
        max_prompt_tokens=10,
        max_completion_tokens=10,
        max_total_tokens=15,
        max_cost_usd=Decimal("0.001000"),
    )
    usage = BudgetUsage(
        prompt_tokens=11,
        completion_tokens=12,
        total_tokens=23,
        estimated_cost_usd=Decimal("0.001001"),
    )

    assert {violation.resource_type for violation in evaluate_budget_violations(budget, usage)} == {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_cost_usd",
    }


def test_delegation_depth_budget_tracking_and_violations() -> None:
    """Verify max_delegation_depth and BudgetUsage delegation recording."""
    budget = ExecutionBudget(max_delegation_depth=2)
    assert budget.max_delegation_depth == 2

    usage = BudgetUsage()
    usage.record_delegation_depth(1)
    assert usage.delegation_depth == 1
    usage.record_delegation_depth(2)
    assert usage.delegation_depth == 2
    usage.record_delegation_depth(1)  # Stays at max reached depth
    assert usage.delegation_depth == 2

    with pytest.raises(ValueError, match="Delegation depth must be non-negative"):
        usage.record_delegation_depth(-1)

    # Within budget
    assert len(evaluate_budget_violations(budget, usage)) == 0

    # Exceeding budget
    usage.record_delegation_depth(3)
    violations = evaluate_budget_violations(budget, usage)
    assert any(v.resource_type == "delegation_depth" and v.actual == 3.0 for v in violations)
