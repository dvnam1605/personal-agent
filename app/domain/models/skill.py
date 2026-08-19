"""Skill models defining reusable multi-step dynamic skills."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import EvidenceType


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


class SkillStep(BaseModel):
    """An individual step in a predefined or synthesized skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_index: int = Field(
        ...,
        ge=0,
        description="0-based sequence index of the step.",
    )
    name: str = Field(
        ...,
        description="Short title of the step.",
    )
    description: str = Field(
        ...,
        description="Operational instruction for the step.",
    )
    action_type: str = Field(
        ...,
        description="Action category (e.g. 'tool_call', 'llm_reasoning', 'evidence_eval').",
    )
    tool_name: str | None = Field(
        default=None,
        description="Tool name to invoke if action_type is 'tool_call'.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters passed to the action or tool.",
    )


class SkillDefinition(BaseModel):
    """Complete specification of a reusable dynamic skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        ...,
        description="Unique skill name (e.g. 'meeting_prep', 'contact_resolution').",
    )
    description: str = Field(
        ...,
        description="High-level description of what the skill accomplishes.",
    )
    category: str = Field(
        ...,
        description="Category classification (e.g. 'research', 'communication', 'calendar').",
    )
    steps: list[SkillStep] = Field(
        default_factory=list,
        description="Sequential execution steps.",
    )
    constraints: SkillConstraints = Field(
        default_factory=SkillConstraints,
        description="Boundaries and resource limits for the skill.",
    )
    completion_criteria: SkillCompletionCriteria = Field(
        ...,
        description="Criteria required for successful completion.",
    )

    @model_validator(mode="after")
    def validate_skill(self) -> "SkillDefinition":
        """Validate step limits, strictly sequential ordering, and tool constraints."""
        if len(self.steps) > self.constraints.max_steps:
            raise ValueError(
                f"Skill '{self.name}' has {len(self.steps)} steps, "
                f"exceeding max_steps constraint ({self.constraints.max_steps})."
            )

        # Enforce strictly sequential 0-based step indices
        expected_indices = list(range(len(self.steps)))
        actual_indices = [step.step_index for step in self.steps]
        if actual_indices != expected_indices:
            raise ValueError(
                f"Skill steps must have sequential 0-based indices {expected_indices}. "
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

        return self
