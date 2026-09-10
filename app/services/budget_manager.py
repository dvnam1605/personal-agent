"""Concurrency-safe atomic budget enforcement manager."""

import asyncio
import time
from collections.abc import Mapping
from decimal import Decimal
from typing import Protocol

import structlog

from app.domain.errors import AppTimeoutError, BudgetExceededError
from app.domain.models import BudgetUsage, ExecutionBudget

logger = structlog.get_logger(__name__)


class DistributedBudgetStore(Protocol):
    """Atomic cross-worker reservation boundary; implementations must fail closed."""

    async def reserve(
        self,
        run_id: str,
        requested: Mapping[str, int],
        limits: Mapping[str, int],
        ttl_seconds: int,
    ) -> bool:
        """Atomically reserve every requested resource or return False without partial allocation."""
        ...


class BudgetManager:
    """Enforces execution limits with atomic reservations and timeout guards."""

    def __init__(
        self,
        budget: ExecutionBudget | None = None,
        usage: BudgetUsage | None = None,
        start_time: float | None = None,
        run_id: str | None = None,
        distributed_store: DistributedBudgetStore | None = None,
    ) -> None:
        self.budget = budget or ExecutionBudget()
        self.usage = usage or BudgetUsage()
        self.start_time = start_time if start_time is not None else time.monotonic()
        self.run_id = run_id
        self.distributed_store = distributed_store
        self._lock = asyncio.Lock()

    @property
    def prompt_tokens(self) -> int:
        """Current reserved/recorded prompt tokens."""
        return self.usage.prompt_tokens

    @property
    def completion_tokens(self) -> int:
        """Current reserved/recorded completion tokens."""
        return self.usage.completion_tokens

    @property
    def total_tokens(self) -> int:
        """Current reserved/recorded total tokens."""
        return self.usage.total_tokens

    @property
    def estimated_cost_usd(self) -> Decimal:
        """Current reserved/recorded cost."""
        return self.usage.estimated_cost_usd

    @property
    def elapsed_seconds(self) -> float:
        """Wall-clock elapsed execution time."""
        return time.monotonic() - self.start_time

    def remaining_seconds(self) -> float:
        """Remaining execution seconds before timeout."""
        remain = self.budget.timeout_seconds - self.elapsed_seconds
        return max(0.0, remain)

    def check_deadline(self) -> None:
        """Check if execution time has exceeded the configured budget timeout."""
        elapsed = self.elapsed_seconds
        self.usage.elapsed_seconds = elapsed
        if elapsed > self.budget.timeout_seconds:
            raise AppTimeoutError(
                message=f"Run timed out after {elapsed:.2f}s (limit: {self.budget.timeout_seconds:.2f}s)",
                details={
                    "timeout_seconds": self.budget.timeout_seconds,
                    "elapsed_seconds": elapsed,
                },
            )

    async def reserve_llm_call(
        self,
        count: int = 1,
        prompt_tokens: int = 0,
        max_completion_tokens: int = 0,
        estimated_cost_usd: Decimal = Decimal("0.000000"),
    ) -> None:
        """Atomically reserve an LLM call plus conservative token and cost headroom."""
        async with self._lock:
            self.check_deadline()
            if count <= 0:
                raise ValueError("LLM reservation count must be positive.")
            if self.usage.llm_calls + count > self.budget.max_llm_calls:
                raise BudgetExceededError(
                    message=f"LLM call budget exceeded: {self.usage.llm_calls + count} > {self.budget.max_llm_calls}",
                    details={
                        "resource_type": "llm_calls",
                        "limit": self.budget.max_llm_calls,
                        "current": self.usage.llm_calls,
                        "requested": count,
                    },
                )
            self._assert_llm_metric_headroom(
                prompt_tokens=prompt_tokens,
                completion_tokens=max_completion_tokens,
                cost_usd=estimated_cost_usd,
            )
            await self._reserve_distributed(
                {
                    "llm_calls": count,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": max_completion_tokens,
                    "total_tokens": prompt_tokens + max_completion_tokens,
                    "cost_micro_usd": int(estimated_cost_usd * Decimal("1000000")),
                }
            )
            self.usage.record_llm_call(count)
            self.usage.record_llm_metrics(
                prompt_tokens=prompt_tokens,
                completion_tokens=max_completion_tokens,
                cost_usd=estimated_cost_usd,
            )

    async def reserve_tool_call(self, count: int = 1) -> None:
        """Atomically verify and reserve capacity for a tool invocation."""
        async with self._lock:
            self.check_deadline()
            if count <= 0:
                raise ValueError("Tool reservation count must be positive.")
            if self.usage.tool_calls + count > self.budget.max_tool_calls:
                raise BudgetExceededError(
                    message=f"Tool call budget exceeded: {self.usage.tool_calls + count} > {self.budget.max_tool_calls}",
                    details={
                        "resource_type": "tool_calls",
                        "limit": self.budget.max_tool_calls,
                        "current": self.usage.tool_calls,
                        "requested": count,
                    },
                )
            await self._reserve_distributed({"tool_calls": count})
            self.usage.record_tool_call(count)

    async def record_react_step(self, count: int = 1) -> None:
        """Atomically record a completed ReAct step."""
        async with self._lock:
            self.check_deadline()
            if self.usage.react_steps + count > self.budget.max_react_steps:
                raise BudgetExceededError(
                    message=f"ReAct step budget exceeded: {self.usage.react_steps + count} > {self.budget.max_react_steps}",
                    details={
                        "resource_type": "react_steps",
                        "limit": self.budget.max_react_steps,
                        "current": self.usage.react_steps,
                    },
                )
            self.usage.record_react_step(count)

    async def record_supervisor_iteration(self, count: int = 1) -> None:
        """Atomically record a completed supervisor iteration."""
        async with self._lock:
            self.check_deadline()
            if self.usage.supervisor_iterations + count > self.budget.max_supervisor_iterations:
                raise BudgetExceededError(
                    message=f"Supervisor iteration budget exceeded: {self.usage.supervisor_iterations + count} > {self.budget.max_supervisor_iterations}",
                    details={
                        "resource_type": "supervisor_iterations",
                        "limit": self.budget.max_supervisor_iterations,
                        "current": self.usage.supervisor_iterations,
                    },
                )
            self.usage.record_supervisor_iteration(count)

    async def record_llm_metrics(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: Decimal = Decimal("0.000000"),
        call_count: int = 1,
    ) -> None:
        """Admit and record an unreserved LLM call through the same hard-budget boundary.

        Provider integrations should normally call ``reserve_llm_call`` before invoking a
        provider. This method remains available for integrations that only receive metrics at
        call completion, but it is itself an admission path: it reserves the call, token, and
        cost counters in the distributed store before mutating local usage.
        """
        async with self._lock:
            self.check_deadline()
            if call_count <= 0:
                raise ValueError("LLM metric call_count must be positive.")
            if self.usage.llm_calls + call_count > self.budget.max_llm_calls:
                raise BudgetExceededError(
                    message=(
                        "LLM call budget exceeded: "
                        f"{self.usage.llm_calls + call_count} > {self.budget.max_llm_calls}"
                    ),
                    details={
                        "resource_type": "llm_calls",
                        "limit": self.budget.max_llm_calls,
                        "current": self.usage.llm_calls,
                        "requested": call_count,
                    },
                )
            self._assert_llm_metric_headroom(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
            )
            await self._reserve_distributed(
                {
                    "llm_calls": call_count,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                    "cost_micro_usd": int(cost_usd * Decimal("1000000")),
                }
            )
            self.usage.record_llm_call(call_count)
            self.usage.record_llm_metrics(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
            )

    def _assert_llm_metric_headroom(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: Decimal,
    ) -> None:
        """Reject a reservation that would exceed any hard LLM token or cost limit."""
        if prompt_tokens < 0 or completion_tokens < 0 or cost_usd < 0:
            raise ValueError("LLM reservation metrics must be non-negative.")

        projected_prompt = self.usage.prompt_tokens + prompt_tokens
        projected_completion = self.usage.completion_tokens + completion_tokens
        projected_total = self.usage.total_tokens + prompt_tokens + completion_tokens
        projected_cost = self.usage.estimated_cost_usd + cost_usd
        checks = (
            ("prompt_tokens", projected_prompt, self.budget.max_prompt_tokens),
            ("completion_tokens", projected_completion, self.budget.max_completion_tokens),
            ("total_tokens", projected_total, self.budget.max_total_tokens),
            ("estimated_cost_usd", projected_cost, self.budget.max_cost_usd),
        )
        for resource_type, projected, limit in checks:
            if projected > limit:
                raise BudgetExceededError(
                    message=f"{resource_type} budget exceeded: {projected} > {limit}",
                    details={
                        "resource_type": resource_type,
                        "limit": str(limit),
                        "current": str(projected),
                    },
                )

    async def _reserve_distributed(self, requested: Mapping[str, int]) -> None:
        """Use a shared atomic store when configured; unavailable stores deny new calls."""
        if self.distributed_store is None:
            return
        if not self.run_id:
            raise BudgetExceededError(
                message="Distributed budget enforcement requires a run ID.",
                details={"resource_type": "distributed_budget"},
            )
        limits = {
            "llm_calls": self.budget.max_llm_calls,
            "tool_calls": self.budget.max_tool_calls,
            "prompt_tokens": self.budget.max_prompt_tokens,
            "completion_tokens": self.budget.max_completion_tokens,
            "total_tokens": self.budget.max_total_tokens,
            "cost_micro_usd": int(self.budget.max_cost_usd * Decimal("1000000")),
        }
        try:
            reserved = await self.distributed_store.reserve(
                self.run_id,
                requested,
                limits,
                max(1, int(self.remaining_seconds()) + 60),
            )
        except Exception as exc:  # noqa: BLE001 - Redis/Lua failures deny the reservation
            logger.error("distributed_budget_store_unavailable", error=str(exc))
            raise BudgetExceededError(
                message="Distributed budget store unavailable; denying new call.",
                details={"resource_type": "distributed_budget"},
            ) from exc
        if not reserved:
            raise BudgetExceededError(
                message="Distributed budget reservation denied.",
                details={"resource_type": "distributed_budget"},
            )
