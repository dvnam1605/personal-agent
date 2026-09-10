"""Route decision model for request triage (P2 + P15)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import (
    Complexity,
    Domain,
    RouteType,
    is_supervisor_route,
    is_workflow_route,
)


class RouteDecision(BaseModel):
    """Result of fast triage routing on an incoming user request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route_type: RouteType = Field(
        ...,
        description="Target routing path (direct specialist, workflow, supervisor, or casual).",
    )
    target_agent: str | None = Field(
        default=None,
        description="Target agent name if route_type is DIRECT_SPECIALIST.",
    )
    target_workflow_id: str | None = Field(
        default=None,
        description="Registered workflow ID/name if route_type is STATIC_WORKFLOW or KNOWN_WORKFLOW.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for this routing decision (0.0 to 1.0).",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters passed to the target route or workflow.",
    )
    reasoning: str = Field(
        default="",
        description="Human-readable explanation of why this route was selected.",
    )

    # Legacy & supporting fields for domain analytics and backward compatibility
    workflow_name: str | None = Field(
        default=None,
        description="Legacy alias for target_workflow_id.",
    )
    domains: list[Domain] = Field(
        min_length=1,
        default_factory=list,
        description="Business domains involved in resolving the request (at least 1 required).",
    )
    complexity: Complexity = Field(
        default=Complexity.DIRECT,
        description="Assessed complexity level of the request.",
    )
    reason_code: str | None = Field(
        default=None,
        description="Machine-readable code or tag explaining the triage rationale.",
    )

    @property
    def is_workflow(self) -> bool:
        """Return True if this route decision targets a static or known workflow."""
        return is_workflow_route(self.route_type)

    @property
    def is_supervisor(self) -> bool:
        """Return True if this route decision targets a supervisor execution path."""
        return is_supervisor_route(self.route_type)

    @model_validator(mode="before")
    @classmethod
    def reconcile_route_fields(cls, data: Any) -> Any:
        """Reconcile P15 target_agent/target_workflow_id with legacy domains/workflow_name."""
        if not isinstance(data, dict):
            return data
        payload = dict(data)

        # Reconcile workflow_name and target_workflow_id
        wf = payload.get("workflow_name") or payload.get("target_workflow_id")
        if wf:
            payload["target_workflow_id"] = wf
            payload["workflow_name"] = wf

        return payload

    @model_validator(mode="after")
    def validate_route_semantics(self) -> "RouteDecision":
        """Enforce semantic invariants between route_type, domains, and workflow_name."""
        is_workflow = is_workflow_route(self.route_type)
        wf_id = self.target_workflow_id or self.workflow_name
        if is_workflow:
            if not wf_id or not wf_id.strip():
                raise ValueError("workflow_name is required when route_type is KNOWN_WORKFLOW.")
        else:
            if wf_id is not None:
                raise ValueError(
                    f"workflow_name must be None when route_type is '{self.route_type}'."
                )

        if self.route_type == RouteType.DIRECT_SPECIALIST:
            if len(self.domains) != 1:
                raise ValueError(
                    f"DIRECT_SPECIALIST route requires exactly 1 domain, got {len(self.domains)}: {self.domains}."
                )

        if self.route_type == RouteType.CASUAL_RESPONSE:
            if self.domains != [Domain.GENERAL]:
                raise ValueError(
                    f"CASUAL_RESPONSE route requires exactly [Domain.GENERAL], got: {self.domains}."
                )

        if not self.domains:
            raise ValueError("domains cannot be empty.")

        return self


__all__ = [
    "RouteDecision",
]
