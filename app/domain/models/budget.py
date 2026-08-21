"""Budget contracts for execution resource tracking and protection."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ExecutionBudget(BaseModel):
    """Resource constraints for an execution run or sub-agent task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_llm_calls: int = Field(
        default=5,
        ge=0,
        description="Maximum allowed primary/generative LLM invocations.",
    )
    max_tool_calls: int = Field(
        default=10,
        ge=0,
        description="Maximum allowed tool executions across the run.",
    )
    max_react_steps: int = Field(
        default=5,
        ge=0,
        description="Maximum reasoning/action iterations in a ReAct loop.",
    )
    max_supervisor_iterations: int = Field(
        default=3,
        ge=0,
        description="Maximum replanning/delegation loops for the Supervisor.",
    )
    max_delegation_depth: int = Field(
        default=3,
        ge=0,
        description="Maximum delegation chain depth allowed.",
    )
    timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Total wall-clock timeout in seconds.",
    )
    max_prompt_tokens: int = Field(
        default=16_000,
        ge=0,
        description="Maximum prompt tokens reserved across the execution.",
    )
    max_completion_tokens: int = Field(
        default=8_000,
        ge=0,
        description="Maximum completion tokens reserved across the execution.",
    )
    max_total_tokens: int = Field(
        default=16_000,
        ge=0,
        description="Maximum combined prompt and completion tokens.",
    )
    max_cost_usd: Decimal = Field(
        default=Decimal("10.000000"),
        ge=Decimal("0"),
        decimal_places=6,
        description="Maximum estimated USD cost reserved across the execution.",
    )


class BudgetUsage(BaseModel):
    """Accumulated resource consumption in an active run with assignment safety."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    react_steps: int = Field(default=0, ge=0)
    supervisor_iterations: int = Field(default=0, ge=0)
    delegation_depth: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: Decimal = Field(default=Decimal("0.000000"), ge=Decimal("0"))

    def record_llm_call(self, count: int = 1) -> None:
        """Increment LLM call counter."""
        if count <= 0:
            raise ValueError(f"Increment count must be positive, got {count}.")
        self.llm_calls += count

    def record_tool_call(self, count: int = 1) -> None:
        """Increment tool call counter."""
        if count <= 0:
            raise ValueError(f"Increment count must be positive, got {count}.")
        self.tool_calls += count

    def record_react_step(self, count: int = 1) -> None:
        """Increment ReAct step counter."""
        if count <= 0:
            raise ValueError(f"Increment count must be positive, got {count}.")
        self.react_steps += count

    def record_supervisor_iteration(self, count: int = 1) -> None:
        """Increment Supervisor iteration counter."""
        if count <= 0:
            raise ValueError(f"Increment count must be positive, got {count}.")
        self.supervisor_iterations += count

    def record_delegation_depth(self, depth: int) -> None:
        """Record or update current maximum delegation depth."""
        if depth < 0:
            raise ValueError(f"Delegation depth must be non-negative, got {depth}.")
        if depth > self.delegation_depth:
            self.delegation_depth = depth

    def record_llm_metrics(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: Decimal = Decimal("0.000000"),
    ) -> None:
        """Record non-negative LLM token and cost consumption or conservative reservations."""
        if prompt_tokens < 0 or completion_tokens < 0 or cost_usd < 0:
            raise ValueError("LLM metrics must be non-negative.")
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += prompt_tokens + completion_tokens
        self.estimated_cost_usd += cost_usd


class BudgetViolation(BaseModel):
    """Report of a breached execution limit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: str = Field(
        ...,
        description="Name of the exhausted resource (e.g. 'llm_calls', 'tool_calls', 'timeout').",
    )
    limit: float = Field(
        ...,
        description="Configured maximum limit.",
    )
    actual: float = Field(
        ...,
        description="Actual consumed quantity at breach point.",
    )
    message: str = Field(
        ...,
        description="Human-readable violation summary.",
    )


def evaluate_budget_violations(
    budget: ExecutionBudget,
    usage: BudgetUsage,
) -> list[BudgetViolation]:
    """Check current usage against budget and return any violations."""
    violations: list[BudgetViolation] = []

    if usage.llm_calls > budget.max_llm_calls:
        violations.append(
            BudgetViolation(
                resource_type="llm_calls",
                limit=float(budget.max_llm_calls),
                actual=float(usage.llm_calls),
                message=(f"LLM call limit exceeded: {usage.llm_calls} > {budget.max_llm_calls}"),
            )
        )

    if usage.tool_calls > budget.max_tool_calls:
        violations.append(
            BudgetViolation(
                resource_type="tool_calls",
                limit=float(budget.max_tool_calls),
                actual=float(usage.tool_calls),
                message=(f"Tool call limit exceeded: {usage.tool_calls} > {budget.max_tool_calls}"),
            )
        )

    if usage.react_steps > budget.max_react_steps:
        violations.append(
            BudgetViolation(
                resource_type="react_steps",
                limit=float(budget.max_react_steps),
                actual=float(usage.react_steps),
                message=(
                    f"ReAct step limit exceeded: {usage.react_steps} > {budget.max_react_steps}"
                ),
            )
        )

    if usage.supervisor_iterations > budget.max_supervisor_iterations:
        violations.append(
            BudgetViolation(
                resource_type="supervisor_iterations",
                limit=float(budget.max_supervisor_iterations),
                actual=float(usage.supervisor_iterations),
                message=(
                    f"Supervisor iteration limit exceeded: "
                    f"{usage.supervisor_iterations} > {budget.max_supervisor_iterations}"
                ),
            )
        )

    if usage.delegation_depth > budget.max_delegation_depth:
        violations.append(
            BudgetViolation(
                resource_type="delegation_depth",
                limit=float(budget.max_delegation_depth),
                actual=float(usage.delegation_depth),
                message=(
                    f"Delegation depth limit exceeded: "
                    f"{usage.delegation_depth} > {budget.max_delegation_depth}"
                ),
            )
        )

    if usage.elapsed_seconds > budget.timeout_seconds:
        violations.append(
            BudgetViolation(
                resource_type="timeout",
                limit=budget.timeout_seconds,
                actual=usage.elapsed_seconds,
                message=(
                    f"Execution timeout exceeded: "
                    f"{usage.elapsed_seconds:.2f}s > {budget.timeout_seconds:.2f}s"
                ),
            )
        )

    if usage.prompt_tokens > budget.max_prompt_tokens:
        violations.append(
            BudgetViolation(
                resource_type="prompt_tokens",
                limit=float(budget.max_prompt_tokens),
                actual=float(usage.prompt_tokens),
                message=(
                    f"Prompt token limit exceeded: {usage.prompt_tokens} > {budget.max_prompt_tokens}"
                ),
            )
        )

    if usage.completion_tokens > budget.max_completion_tokens:
        violations.append(
            BudgetViolation(
                resource_type="completion_tokens",
                limit=float(budget.max_completion_tokens),
                actual=float(usage.completion_tokens),
                message=(
                    "Completion token limit exceeded: "
                    f"{usage.completion_tokens} > {budget.max_completion_tokens}"
                ),
            )
        )

    if usage.total_tokens > budget.max_total_tokens:
        violations.append(
            BudgetViolation(
                resource_type="total_tokens",
                limit=float(budget.max_total_tokens),
                actual=float(usage.total_tokens),
                message=(
                    f"Total token limit exceeded: {usage.total_tokens} > {budget.max_total_tokens}"
                ),
            )
        )

    if usage.estimated_cost_usd > budget.max_cost_usd:
        violations.append(
            BudgetViolation(
                resource_type="estimated_cost_usd",
                limit=float(budget.max_cost_usd),
                actual=float(usage.estimated_cost_usd),
                message=(
                    "Estimated cost limit exceeded: "
                    f"{usage.estimated_cost_usd} > {budget.max_cost_usd}"
                ),
            )
        )

    return violations
