"""Unit tests for atomic BudgetManager, concurrent reservations, and limits."""

import asyncio
import time
from collections.abc import Mapping
from decimal import Decimal

import pytest

from app.domain.errors import AppTimeoutError, BudgetExceededError
from app.domain.models import ExecutionBudget
from app.services.platform.budget_manager import BudgetManager


@pytest.mark.asyncio
async def test_budget_manager_reserve_llm_calls_within_limit() -> None:
    """Verify reserving LLM calls increments usage up to budget limit."""
    budget = ExecutionBudget(max_llm_calls=3)
    mgr = BudgetManager(budget=budget)

    await mgr.reserve_llm_call(1)
    assert mgr.usage.llm_calls == 1

    await mgr.reserve_llm_call(2)
    assert mgr.usage.llm_calls == 3

    # Exceeding budget raises BudgetExceededError
    with pytest.raises(BudgetExceededError, match="LLM call budget exceeded"):
        await mgr.reserve_llm_call(1)


@pytest.mark.asyncio
async def test_budget_manager_reserve_tool_calls_within_limit() -> None:
    """Verify reserving tool calls increments usage up to budget limit."""
    budget = ExecutionBudget(max_tool_calls=2)
    mgr = BudgetManager(budget=budget)

    await mgr.reserve_tool_call(2)
    assert mgr.usage.tool_calls == 2

    with pytest.raises(BudgetExceededError, match="Tool call budget exceeded"):
        await mgr.reserve_tool_call(1)


@pytest.mark.asyncio
async def test_budget_manager_react_and_supervisor_steps() -> None:
    """Verify ReAct and supervisor steps recording and limit enforcement."""
    budget = ExecutionBudget(max_react_steps=2, max_supervisor_iterations=1)
    mgr = BudgetManager(budget=budget)

    await mgr.record_react_step(1)
    await mgr.record_react_step(1)
    assert mgr.usage.react_steps == 2

    with pytest.raises(BudgetExceededError, match="ReAct step budget exceeded"):
        await mgr.record_react_step(1)

    await mgr.record_supervisor_iteration(1)
    with pytest.raises(BudgetExceededError, match="Supervisor iteration budget exceeded"):
        await mgr.record_supervisor_iteration(1)


@pytest.mark.asyncio
async def test_budget_manager_concurrent_safety() -> None:
    """Verify atomic reservation prevents race conditions across concurrent tasks."""
    budget = ExecutionBudget(max_llm_calls=10)
    mgr = BudgetManager(budget=budget)

    # Launch 20 concurrent reservation attempts of 1 call each
    async def try_reserve():
        try:
            await mgr.reserve_llm_call(1)
            return True
        except BudgetExceededError:
            return False

    results = await asyncio.gather(*[try_reserve() for _ in range(20)])
    successful_count = sum(1 for r in results if r is True)
    failed_count = sum(1 for r in results if r is False)

    assert successful_count == 10
    assert failed_count == 10
    assert mgr.usage.llm_calls == 10


@pytest.mark.asyncio
async def test_budget_manager_deadline_timeout() -> None:
    """Verify deadline timeout raises AppTimeoutError."""
    # Set timeout to 0.05s and artificially shift start_time back
    budget = ExecutionBudget(timeout_seconds=0.05)
    mgr = BudgetManager(budget=budget, start_time=time.monotonic() - 0.1)

    with pytest.raises(AppTimeoutError, match="Run timed out"):
        await mgr.reserve_llm_call(1)


@pytest.mark.asyncio
async def test_budget_manager_metrics_accumulation() -> None:
    """Verify token and cost accumulation."""
    mgr = BudgetManager()
    await mgr.record_llm_metrics(
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=Decimal("0.000300"),
    )
    await mgr.record_llm_metrics(
        prompt_tokens=200,
        completion_tokens=100,
        cost_usd=Decimal("0.000600"),
    )

    assert mgr.prompt_tokens == 300
    assert mgr.completion_tokens == 150
    assert mgr.total_tokens == 450
    assert mgr.estimated_cost_usd == Decimal("0.000900")


@pytest.mark.asyncio
async def test_budget_manager_reserves_token_and_cost_headroom_before_llm_call() -> None:
    """Verify an LLM call cannot start if its conservative output or cost reservation exceeds budget."""
    mgr = BudgetManager(
        budget=ExecutionBudget(
            max_llm_calls=2,
            max_prompt_tokens=100,
            max_completion_tokens=50,
            max_total_tokens=120,
            max_cost_usd=Decimal("0.010000"),
        )
    )

    await mgr.reserve_llm_call(
        prompt_tokens=60,
        max_completion_tokens=40,
        estimated_cost_usd=Decimal("0.005000"),
    )
    assert mgr.total_tokens == 100

    with pytest.raises(BudgetExceededError, match="completion_tokens budget exceeded"):
        await mgr.reserve_llm_call(prompt_tokens=1, max_completion_tokens=11)

    with pytest.raises(BudgetExceededError, match="estimated_cost_usd budget exceeded"):
        await mgr.reserve_llm_call(estimated_cost_usd=Decimal("0.006000"))


class _UnavailableDistributedStore:
    async def reserve(self, *args, **kwargs):
        raise ConnectionError("redis unavailable")


class _SharedDistributedStore:
    """In-memory stand-in for Redis's atomic script, shared by independent workers."""

    def __init__(self) -> None:
        self.reservations: dict[str, dict[str, int]] = {}
        self._lock = asyncio.Lock()

    async def reserve(
        self,
        run_id: str,
        requested: Mapping[str, int],
        limits: Mapping[str, int],
        ttl_seconds: int,
    ) -> bool:
        del ttl_seconds
        async with self._lock:
            current = self.reservations.setdefault(run_id, {})
            if any(current.get(key, 0) + amount > limits[key] for key, amount in requested.items()):
                return False
            for key, amount in requested.items():
                current[key] = current.get(key, 0) + amount
            return True


@pytest.mark.asyncio
async def test_budget_manager_fails_closed_when_distributed_store_is_unavailable() -> None:
    """Verify a multi-worker budget never falls back to a local-only reservation."""
    mgr = BudgetManager(
        budget=ExecutionBudget(max_llm_calls=1),
        run_id="run_distributed_1",
        distributed_store=_UnavailableDistributedStore(),
    )

    with pytest.raises(BudgetExceededError, match="Distributed budget store unavailable"):
        await mgr.reserve_llm_call()
    assert mgr.usage.llm_calls == 0


@pytest.mark.asyncio
async def test_budget_manager_enforces_one_shared_budget_across_workers() -> None:
    """Verify separate workers cannot over-reserve the same distributed run budget."""
    store = _SharedDistributedStore()
    budget = ExecutionBudget(max_llm_calls=3)
    workers = [
        BudgetManager(budget=budget, run_id="shared_run", distributed_store=store),
        BudgetManager(budget=budget, run_id="shared_run", distributed_store=store),
    ]

    async def try_reserve(worker: BudgetManager) -> bool:
        try:
            await worker.reserve_llm_call()
            return True
        except BudgetExceededError:
            return False

    results = await asyncio.gather(*(try_reserve(workers[index % 2]) for index in range(10)))

    assert sum(results) == 3
    assert store.reservations["shared_run"]["llm_calls"] == 3
    assert sum(worker.usage.llm_calls for worker in workers) == 3


@pytest.mark.asyncio
async def test_record_llm_metrics_uses_distributed_admission_path() -> None:
    """Completion-only integrations cannot bypass shared call/token/cost limits."""
    store = _SharedDistributedStore()
    budget = ExecutionBudget(
        max_llm_calls=2,
        max_prompt_tokens=20,
        max_completion_tokens=20,
        max_total_tokens=30,
        max_cost_usd=Decimal("0.010000"),
    )
    workers = [
        BudgetManager(budget=budget, run_id="metrics_run", distributed_store=store),
        BudgetManager(budget=budget, run_id="metrics_run", distributed_store=store),
    ]

    async def record(worker: BudgetManager) -> bool:
        try:
            await worker.record_llm_metrics(
                prompt_tokens=5,
                completion_tokens=5,
                cost_usd=Decimal("0.001000"),
            )
            return True
        except BudgetExceededError:
            return False

    results = await asyncio.gather(*(record(workers[index % 2]) for index in range(6)))
    assert sum(results) == 2
    assert store.reservations["metrics_run"]["llm_calls"] == 2
    assert store.reservations["metrics_run"]["total_tokens"] == 20
