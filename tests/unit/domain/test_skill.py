"""Unit tests for Skill models, step ordering, and constraint enforcement."""

import pytest
from pydantic import ValidationError

from app.domain.enums import EvidenceType
from app.domain.models import (
    SkillCompletionCriteria,
    SkillConstraints,
    SkillDefinition,
    SkillMetadata,
    SkillStep,
)


def _metadata(**overrides: object) -> SkillMetadata:
    values: dict[str, object] = {
        "name": "email-meeting-scheduler",
        "version": "1.0.0",
        "description": "Automates finding times and preparing follow-ups.",
        "required_capabilities": ["gmail.read", "calendar.read"],
    }
    values.update(overrides)
    return SkillMetadata(**values)  # type: ignore[arg-type]


def _step(index: int, name: str = "Step", **overrides: object) -> SkillStep:
    values: dict[str, object] = {
        "step_index": index,
        "name": name,
        "description": f"{name} description",
    }
    values.update(overrides)
    return SkillStep(**values)  # type: ignore[arg-type]


def test_valid_skill_definition() -> None:
    """Verify the P14 skill structure with sequential 1-based steps."""
    skill = SkillDefinition(
        metadata=_metadata(),
        steps=[
            _step(1, "Fetch Email Context", tool_name="gmail.search_messages"),
            _step(2, "Check Free Slots", tool_name="calendar.get_free_busy"),
        ],
        completion_criteria="Draft email prepared with available calendar slots.",
        constraints=SkillConstraints(
            max_steps=3,
            timeout_seconds=20.0,
            allowed_tools=["gmail.search_messages", "calendar.get_free_busy"],
            allow_mutations=False,
        ),
    )

    assert skill.name == "email-meeting-scheduler"
    assert skill.version == "1.0.0"
    assert len(skill.steps) == 2
    assert skill.constraints.allow_mutations is False
    assert skill.metadata.is_mutation is False


def test_skill_metadata_rejects_invalid_version_and_name() -> None:
    """Registry identity must be a slug plus MAJOR.MINOR.PATCH."""
    with pytest.raises(ValidationError, match="version"):
        SkillMetadata(
            name="meeting-prep",
            version="1.0",
            description="Prepare a structured meeting brief from sources.",
        )
    with pytest.raises(ValidationError):
        SkillMetadata(
            name="MeetingPrep",
            version="1.0.0",
            description="Prepare a structured meeting brief from sources.",
        )


def test_skill_step_limit_exceeded() -> None:
    """Verify error when steps count exceeds max_steps constraint."""
    with pytest.raises(ValidationError, match="exceeding max_steps constraint"):
        SkillDefinition(
            metadata=_metadata(name="too-many-steps"),
            steps=[_step(1, "S1"), _step(2, "S2")],
            completion_criteria="Done",
            constraints=SkillConstraints(max_steps=1),
        )


def test_skill_disallowed_tool_rejected() -> None:
    """Verify error when a step references a tool not in allowed_tools."""
    with pytest.raises(ValidationError, match="not in allowed_tools list"):
        SkillDefinition(
            metadata=_metadata(name="unauthorized-tool-skill"),
            steps=[_step(1, "Delete All", tool_name="gmail.delete_message")],
            completion_criteria="Done",
            constraints=SkillConstraints(allowed_tools=["gmail.search_messages"]),
        )


def test_skill_non_sequential_or_duplicate_step_indices_rejected() -> None:
    """Verify skill rejects duplicate, skipped, or out-of-order step indices."""
    with pytest.raises(ValidationError, match="sequential 1-based indices"):
        SkillDefinition(
            metadata=_metadata(name="bad-order"),
            steps=[_step(2, "S2"), _step(1, "S1")],
            completion_criteria="Done",
            constraints=SkillConstraints(max_steps=5),
        )
    with pytest.raises(ValidationError, match="sequential 1-based indices"):
        SkillDefinition(
            metadata=_metadata(name="skipped-idx"),
            steps=[_step(1, "S1"), _step(3, "S3")],
            completion_criteria="Done",
            constraints=SkillConstraints(max_steps=5),
        )
    with pytest.raises(ValidationError, match="sequential 1-based indices"):
        SkillDefinition(
            metadata=_metadata(name="duplicate-idx"),
            steps=[_step(1, "S1"), _step(1, "S1-dup")],
            completion_criteria="Done",
            constraints=SkillConstraints(max_steps=5),
        )


def test_zero_based_step_index_rejected() -> None:
    """P14 steps are 1-based."""
    with pytest.raises(ValidationError):
        SkillStep(step_index=0, name="S0", description="legacy index")


def test_mutation_flags_must_agree() -> None:
    """metadata.is_mutation and constraints.allow_mutations cannot disagree."""
    with pytest.raises(ValidationError, match="mutation flags disagree"):
        SkillDefinition(
            metadata=_metadata(name="draft-skill", is_mutation=True),
            steps=[_step(1, "Draft", tool_name="gmail.create_draft")],
            completion_criteria="Draft prepared.",
            constraints=SkillConstraints(allow_mutations=False, max_steps=2),
        )


def test_allow_mutations_inherited_when_omitted() -> None:
    """Unset constraints.allow_mutations follows metadata.is_mutation."""
    inherited = SkillDefinition(
        metadata=_metadata(name="draft-skill", is_mutation=True),
        steps=[_step(1, "Draft", tool_name="gmail.create_draft")],
        completion_criteria="Draft prepared.",
        constraints=SkillConstraints(max_steps=2),
    )
    assert inherited.constraints.allow_mutations is True
    defaulted = SkillDefinition(
        metadata=_metadata(name="draft-skill-default", is_mutation=True),
        steps=[_step(1, "Draft", tool_name="gmail.create_draft")],
        completion_criteria="Draft prepared.",
    )
    assert defaulted.constraints.allow_mutations is True
    from_dict = SkillDefinition.model_validate(
        {
            "metadata": {
                "name": "draft-from-dict",
                "version": "1.0.0",
                "description": "Loaded from a raw mapping payload.",
                "is_mutation": True,
            },
            "steps": [
                {
                    "step_index": 1,
                    "name": "Draft",
                    "description": "Draft description",
                    "tool_name": "gmail.create_draft",
                }
            ],
            "completion_criteria": "Draft prepared.",
            "constraints": {"max_steps": 2, "allow_mutations": True},
        }
    )
    assert from_dict.constraints.allow_mutations is True
    inherited_from_dict = SkillDefinition.model_validate(
        {
            "metadata": {
                "name": "draft-from-dict-inherit",
                "version": "1.0.0",
                "description": "Loaded from a raw mapping payload.",
                "is_mutation": True,
            },
            "steps": [
                {
                    "step_index": 1,
                    "name": "Draft",
                    "description": "Draft description",
                    "tool_name": "gmail.create_draft",
                }
            ],
            "completion_criteria": "Draft prepared.",
            "constraints": {"max_steps": 2},
        }
    )
    assert inherited_from_dict.constraints.allow_mutations is True
    cloned = SkillDefinition.model_validate(defaulted)
    assert cloned.constraints.allow_mutations is True
    with pytest.raises(ValidationError):
        SkillDefinition.model_validate(
            {
                "metadata": "not-metadata",
                "steps": [{"step_index": 1, "name": "S", "description": "d"}],
                "completion_criteria": "Done.",
            }
        )
    with pytest.raises(ValidationError):
        SkillDefinition.model_validate(
            {
                "metadata": {
                    "name": "bad-constraints",
                    "version": "1.0.0",
                    "description": "This description is long enough.",
                },
                "steps": [{"step_index": 1, "name": "S", "description": "d"}],
                "completion_criteria": "Done.",
                "constraints": 1,
            }
        )


def test_blank_labels_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillMetadata(
            name="blank-tag",
            version="1.0.0",
            description="This description is long enough.",
            tags=[" "],
        )
    with pytest.raises(ValidationError):
        SkillStep(step_index=1, name="   ", description="desc")


def test_skill_completion_criteria_and_constraints_remain_typed() -> None:
    """P2 supporting types stay independently valid."""
    criteria = SkillCompletionCriteria(
        required_evidence_types=[EvidenceType.EMAIL, EvidenceType.CALENDAR_EVENT],
        success_condition="Draft email prepared with available calendar slots.",
    )
    constraints = SkillConstraints(max_steps=4, timeout_seconds=15.0)
    assert criteria.success_condition.startswith("Draft")
    assert constraints.max_steps == 4
