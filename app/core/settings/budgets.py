"""Execution budgets: ReAct and supervisor limits, per-route LLM targets and timeouts."""

from pydantic import BaseModel, Field


class ReActBudgetSettings(BaseModel):
    max_steps: int = Field(default=6, description="Max reasoning steps in specialist ReAct loop")
    max_tool_calls: int = Field(default=8, description="Max total tool calls per ReAct session")
    timeout_seconds: float = Field(default=30.0, description="ReAct execution timeout in seconds")
    stop_on_no_progress: bool = Field(
        default=True, description="Detect and stop identical consecutive tool calls"
    )


class SupervisorBudgetSettings(BaseModel):
    max_iterations: int = Field(default=5, description="Max task execution iterations")
    max_replans: int = Field(default=2, description="Max allowed replanning attempts")
    max_parallel_tasks: int = Field(default=4, description="Max parallel sub-tasks")
    timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Wall-clock timeout for a Supervisor DAG run (aligned with ExecutionBudget).",
    )
    cost_per_task_usd: float = Field(
        default=0.02,
        ge=0.0,
        description="Estimated USD cost per planned specialist task for DAG budget checks.",
    )


class RouteBudget(BaseModel):
    target_llm_calls: int
    target_latency_seconds: float
    max_tokens: int


class LLMBudgetSettings(BaseModel):
    direct_specialist: RouteBudget = Field(
        default_factory=lambda: RouteBudget(
            target_llm_calls=1, target_latency_seconds=2.0, max_tokens=2000
        )
    )
    specialist_react: RouteBudget = Field(
        default_factory=lambda: RouteBudget(
            target_llm_calls=3, target_latency_seconds=5.0, max_tokens=8000
        )
    )
    known_workflow: RouteBudget = Field(
        default_factory=lambda: RouteBudget(
            target_llm_calls=2, target_latency_seconds=3.5, max_tokens=6000
        )
    )
    supervisor_dag: RouteBudget = Field(
        default_factory=lambda: RouteBudget(
            target_llm_calls=5, target_latency_seconds=10.0, max_tokens=16000
        )
    )


class TimeoutsSettings(BaseModel):
    google_api_seconds: float = Field(default=10.0, description="Google API request timeout")
    web_search_seconds: float = Field(default=8.0, description="Web search provider timeout")
    llm_request_seconds: float = Field(default=90.0, description="LLM provider request timeout")
    database_query_seconds: float = Field(default=5.0, description="Database query timeout")
