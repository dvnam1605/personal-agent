"""Route decision model for request triage."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import Complexity, Domain, RouteType


class RouteDecision(BaseModel):
    """Result of fast triage routing on an incoming user request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domains: list[Domain] = Field(
        ...,
        min_length=1,
        description="Business domains involved in resolving the request (at least 1 required).",
    )
    complexity: Complexity = Field(
        ...,
        description="Assessed complexity level of the request.",
    )
    route_type: RouteType = Field(
        ...,
        description="Target routing path (direct specialist, workflow, supervisor, or casual).",
    )
    workflow_name: str | None = Field(
        default=None,
        description="Name of the registered workflow if route_type is KNOWN_WORKFLOW.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for this routing decision (0.0 to 1.0).",
    )
    reason_code: str | None = Field(
        default=None,
        description="Machine-readable code or tag explaining the triage rationale.",
    )

    @model_validator(mode="after")
    def validate_route_semantics(self) -> "RouteDecision":
        """Enforce semantic invariants between route_type, domains, and workflow_name."""
        if self.route_type == RouteType.KNOWN_WORKFLOW:
            if not self.workflow_name or not self.workflow_name.strip():
                raise ValueError("workflow_name is required when route_type is KNOWN_WORKFLOW.")
        else:
            if self.workflow_name is not None:
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

        return self
