"""AssistantState domain model representing runtime execution state."""

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import RunStatus
from app.domain.models.approvals.action import ActionApproval, ProposedAction
from app.domain.models.platform.agent import TaskResult
from app.domain.models.retrieval.evidence import EvidenceItem
from app.domain.models.routing.route import RouteDecision
from app.domain.models.supervisor.plan import ExecutionPlan


class AssistantState(BaseModel):
    """Central typed execution state passed through the assistant lifecycle."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    run_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique run ID for the assistant execution.",
    )
    user_id: str = Field(
        ...,
        description="Authenticated user identifier.",
    )
    request: str = Field(
        ...,
        description="Raw initial user request text.",
    )
    normalized_request: str | None = Field(
        default=None,
        description="Normalized or preprocessed user prompt.",
    )
    route_decision: RouteDecision | None = Field(
        default=None,
        description="Fast triage route classification.",
    )
    goal: str | None = Field(
        default=None,
        description="Synthesized operational goal for this run.",
    )
    entities: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted or resolved named entities (people, dates, files, etc.).",
    )
    active_skill: str | None = Field(
        default=None,
        description="Name of currently active dynamic skill if any.",
    )
    active_workflow: str | None = Field(
        default=None,
        description="Name of currently active static workflow if any.",
    )
    plan: ExecutionPlan | None = Field(
        default=None,
        description="Active multi-task execution plan.",
    )
    task_results: list[TaskResult] = Field(
        default_factory=list,
        description="Results of completed sub-tasks.",
    )
    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="Grounded evidence items accumulated across turns.",
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="Specific missing parameters or questions needed from user/subsystems.",
    )
    proposed_actions: list[ProposedAction] = Field(
        default_factory=list,
        description="Typed actions proposed by agents requiring policy validation or human confirmation.",
    )
    approvals: list[ActionApproval] = Field(
        default_factory=list,
        description="Recorded human approval decisions for proposed mutation actions.",
    )
    continuation_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Durable, resumable context supplied when a waiting run continues.",
    )
    task_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Bounded execution context for the active task or workflow node.",
    )
    iteration: int = Field(
        default=0,
        ge=0,
        description="Current supervisor or high-level iteration count.",
    )
    react_steps: int = Field(
        default=0,
        ge=0,
        description="Total ReAct reasoning steps executed across all agents.",
    )
    tool_call_count: int = Field(
        default=0,
        ge=0,
        description="Total tool executions performed in this run.",
    )
    llm_call_count: int = Field(
        default=0,
        ge=0,
        description="Total LLM calls invoked in this run.",
    )
    total_tokens: int = Field(
        default=0,
        ge=0,
        description="Cumulative tokens consumed across all model invocations.",
    )
    prompt_tokens: int = Field(
        default=0,
        ge=0,
        description="Cumulative prompt tokens consumed across model invocations.",
    )
    completion_tokens: int = Field(
        default=0,
        ge=0,
        description="Cumulative completion tokens consumed across model invocations.",
    )
    elapsed_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Cumulative execution time in seconds.",
    )
    supervisor_iterations: int = Field(
        default=0,
        ge=0,
        description="Cumulative supervisor iterations completed.",
    )
    estimated_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Cumulative estimated USD cost of model invocations.",
    )
    max_delegation_depth_reached: int = Field(
        default=0,
        ge=0,
        description="Maximum depth of agent delegation reached in this run.",
    )
    status: RunStatus = Field(
        default=RunStatus.PENDING,
        description="Current lifecycle status of the run.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="List of recorded error messages or exceptions.",
    )

    def add_evidence(self, item: EvidenceItem) -> None:
        """Add an evidence item to the state."""
        self.evidence.append(item)

    def record_error(self, message: str) -> None:
        """Record an error and mark status failed if running."""
        self.errors.append(message)
        if self.status in (RunStatus.PENDING, RunStatus.RUNNING):
            self.status = RunStatus.FAILED
