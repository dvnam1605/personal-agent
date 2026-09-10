"""Skill models defining reusable multi-step dynamic skills (P2 + P14).

P14 is the production contract: ``SkillMetadata`` + 1-based ``SkillStep`` +
``SkillDefinition``. P2 ``SkillConstraints`` / ``SkillCompletionCriteria`` remain
as supporting types so execution budgets and evidence completion rules stay
typed without a second skill schema.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import EvidenceType


def _normalize_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} cannot be blank.")
    return normalized


def _normalize_unique_labels(values: list[str], *, field: str) -> list[str]:
    normalized: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            raise ValueError(f"{field} entries cannot be blank.")
        if item not in normalized:
            normalized.append(item)
    return normalized


class SkillConstraints(BaseModel):
    """Execution constraints and safety boundaries for a dynamic skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(
        default=5,
        ge=1,
        description="Maximum sequential steps allowed in the skill execution.",
    )
    timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Maximum wall-clock execution time.",
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Allowed tool names. If empty, all non-mutation agent tools are permitted.",
    )
    allow_mutations: bool = Field(
        default=False,
        description="Whether this skill is permitted to perform mutation operations.",
    )

    @field_validator("allowed_tools", mode="after")
    @classmethod
    def normalize_allowed_tools(cls, values: list[str]) -> list[str]:
        """Canonicalize tool names and reject blank entries."""
        return _normalize_unique_labels(values, field="allowed_tools")


class SkillCompletionCriteria(BaseModel):
    """Conditions that must be met for a skill execution to be considered complete."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_evidence_types: list[EvidenceType] = Field(
        default_factory=list,
        description="Evidence types that must be collected during execution.",
    )
    success_condition: str = Field(
        ...,
        description="Description or rule specifying when the skill goal has been met.",
    )

    @field_validator("success_condition", mode="after")
    @classmethod
    def normalize_success_condition(cls, value: str) -> str:
        """Reject a blank success condition."""
        return _normalize_text(value, field="success_condition")


class SkillMetadata(BaseModel):
    """Metadata and capability requirements for a registered skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(min_length=10, max_length=500)
    required_capabilities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(
        default_factory=list,
        description="Intent trigger phrases or regex keywords for skill matching.",
    )
    is_mutation: bool = Field(
        default=False,
        description="Whether this skill involves write or send operations requiring approval.",
    )

    @field_validator("name", "description", mode="after")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        """Canonicalize required identity text."""
        return _normalize_text(value, field="skill metadata text")

    @field_validator("required_capabilities", "tags", "triggers", mode="after")
    @classmethod
    def normalize_label_lists(cls, values: list[str]) -> list[str]:
        """Canonicalize capability, tag, and trigger labels."""
        return _normalize_unique_labels(values, field="skill metadata list")


class SkillStep(BaseModel):
    """A single procedural step in a skill workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_index: int = Field(ge=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required_capability: str | None = None
    expected_outputs: list[str] = Field(default_factory=list)
    is_optional: bool = Field(default=False)
    tool_name: str | None = Field(
        default=None,
        description="Optional registered tool this step is expected to invoke.",
    )

    @field_validator("name", "description", mode="after")
    @classmethod
    def normalize_step_text(cls, value: str) -> str:
        """Reject blank step titles and instructions."""
        return _normalize_text(value, field="skill step text")

    @field_validator("required_capability", "tool_name", mode="after")
    @classmethod
    def normalize_optional_labels(cls, value: str | None) -> str | None:
        """Reject explicitly blank optional labels."""
        if value is None:
            return None
        return _normalize_text(value, field="skill step label")

    @field_validator("expected_outputs", mode="after")
    @classmethod
    def normalize_expected_outputs(cls, values: list[str]) -> list[str]:
        """Canonicalize expected output names."""
        return _normalize_unique_labels(values, field="expected_outputs")


class SkillDefinition(BaseModel):
    """Complete specification of a reusable skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata: SkillMetadata
    inputs_schema: dict[str, Any] = Field(default_factory=dict)
    steps: list[SkillStep] = Field(min_length=1)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    safety_constraints: list[str] = Field(default_factory=list)
    completion_criteria: str = Field(min_length=1)
    guidance: str = Field(
        default="",
        description="Verbatim SKILL.md body used as procedural guidance, never evaluated as code.",
    )
    constraints: SkillConstraints = Field(default_factory=SkillConstraints)

    @field_validator("completion_criteria", mode="after")
    @classmethod
    def normalize_completion_criteria(cls, value: str) -> str:
        """Reject a blank completion rule."""
        return _normalize_text(value, field="completion_criteria")

    @field_validator("safety_constraints", mode="after")
    @classmethod
    def normalize_safety_constraints(cls, values: list[str]) -> list[str]:
        """Canonicalize safety constraint sentences."""
        return _normalize_unique_labels(values, field="safety_constraints")

    @property
    def name(self) -> str:
        """Registry identity borrowed from metadata."""
        return self.metadata.name

    @property
    def version(self) -> str:
        """Semantic version borrowed from metadata."""
        return self.metadata.version

    @model_validator(mode="before")
    @classmethod
    def inherit_allow_mutations(cls, data: Any) -> Any:
        """Default ``constraints.allow_mutations`` from ``metadata.is_mutation``.

        Authors still fail closed when both flags are set explicitly and disagree.
        """
        if not isinstance(data, dict):  # pragma: no cover - pydantic instance path
            return data
        metadata = data.get("metadata")
        if isinstance(metadata, SkillMetadata):
            is_mutation = metadata.is_mutation
        elif isinstance(metadata, dict):
            is_mutation = bool(metadata.get("is_mutation", False))
        else:
            return data

        constraints = data.get("constraints")
        if constraints is None:
            return {**data, "constraints": {"allow_mutations": is_mutation}}
        if isinstance(constraints, dict):
            if "allow_mutations" in constraints:
                return data
            return {**data, "constraints": {**constraints, "allow_mutations": is_mutation}}
        if isinstance(constraints, SkillConstraints):
            if "allow_mutations" in constraints.model_fields_set:
                return data
            return {
                **data,
                "constraints": constraints.model_copy(update={"allow_mutations": is_mutation}),
            }
        return data

    @model_validator(mode="after")
    def validate_skill(self) -> "SkillDefinition":
        """Validate step limits, sequential ordering, tools, and mutation flags."""
        if len(self.steps) > self.constraints.max_steps:
            raise ValueError(
                f"Skill '{self.metadata.name}' has {len(self.steps)} steps, "
                f"exceeding max_steps constraint ({self.constraints.max_steps})."
            )

        expected_indices = list(range(1, len(self.steps) + 1))
        actual_indices = [step.step_index for step in self.steps]
        if actual_indices != expected_indices:
            raise ValueError(
                f"Skill steps must have sequential 1-based indices {expected_indices}. "
                f"Found: {actual_indices}."
            )

        if self.constraints.allowed_tools:
            allowed_set = set(self.constraints.allowed_tools)
            for step in self.steps:
                if step.tool_name and step.tool_name not in allowed_set:
                    raise ValueError(
                        f"Step '{step.name}' references tool '{step.tool_name}' "
                        f"which is not in allowed_tools list."
                    )

        if self.metadata.is_mutation != self.constraints.allow_mutations:
            raise ValueError(
                f"Skill '{self.metadata.name}' mutation flags disagree: "
                f"metadata.is_mutation={self.metadata.is_mutation}, "
                f"constraints.allow_mutations={self.constraints.allow_mutations}."
            )

        return self
