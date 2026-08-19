"""Unit tests for Skill models, step ordering, and constraint enforcement."""

import pytest
from pydantic import ValidationError

from app.domain.enums import EvidenceType
from app.domain.models import (
    SkillCompletionCriteria,
    SkillConstraints,
    SkillDefinition,
    SkillStep,
)


def test_valid_skill_definition() -> None:
    """Verify standard dynamic skill structure with sequential 0-based steps."""
    criteria = SkillCompletionCriteria(
        required_evidence_types=[EvidenceType.EMAIL, EvidenceType.CALENDAR_EVENT],
        success_condition="Draft email prepared with available calendar slots.",
    )
    constraints = SkillConstraints(
        max_steps=3,
        timeout_seconds=20.0,
        allowed_tools=["gmail.list_messages", "calendar.get_free_busy"],
        allow_mutations=False,
    )
    steps = [
        SkillStep(
            step_index=0,
            name="Fetch Email Context",
            description="Query messages for thread context",
            action_type="tool_call",
            tool_name="gmail.list_messages",
        ),
        SkillStep(
            step_index=1,
            name="Check Free Slots",
            description="Check calendar availability",
            action_type="tool_call",
            tool_name="calendar.get_free_busy",
        ),
    ]

    skill = SkillDefinition(
        name="email_meeting_scheduler",
        description="Automates finding times and preparing follow-ups",
        category="communication",
        steps=steps,
        constraints=constraints,
        completion_criteria=criteria,
    )

    assert skill.name == "email_meeting_scheduler"
    assert len(skill.steps) == 2
    assert skill.constraints.allow_mutations is False


def test_skill_step_limit_exceeded() -> None:
    """Verify error when steps count exceeds max_steps constraint."""
    criteria = SkillCompletionCriteria(success_condition="Done")
    constraints = SkillConstraints(max_steps=1)
    steps = [
        SkillStep(
            step_index=0,
            name="S1",
            description="Step 1",
            action_type="reasoning",
        ),
        SkillStep(
            step_index=1,
            name="S2",
            description="Step 2",
            action_type="reasoning",
        ),
    ]
    with pytest.raises(ValidationError, match="exceeding max_steps constraint"):
        SkillDefinition(
            name="too_many_steps",
            description="Test",
            category="general",
            steps=steps,
            constraints=constraints,
            completion_criteria=criteria,
        )


def test_skill_disallowed_tool_rejected() -> None:
    """Verify error when a step references a tool not in allowed_tools."""
    criteria = SkillCompletionCriteria(success_condition="Done")
    constraints = SkillConstraints(allowed_tools=["gmail.list_messages"])
    steps = [
        SkillStep(
            step_index=0,
            name="Delete All",
            description="Unauthorized delete",
            action_type="tool_call",
            tool_name="gmail.delete_message",
        )
    ]
    with pytest.raises(ValidationError, match="not in allowed_tools list"):
        SkillDefinition(
            name="unauthorized_tool_skill",
            description="Test",
            category="communication",
            steps=steps,
            constraints=constraints,
            completion_criteria=criteria,
        )


def test_skill_non_sequential_or_duplicate_step_indices_rejected() -> None:
    """Verify skill rejects duplicate, skipped, or out-of-order step indices."""
    criteria = SkillCompletionCriteria(success_condition="Done")
    constraints = SkillConstraints(max_steps=5)

    # Out-of-order indices
    steps_out_of_order = [
        SkillStep(step_index=1, name="S1", description="D1", action_type="reasoning"),
        SkillStep(step_index=0, name="S0", description="D0", action_type="reasoning"),
    ]
    with pytest.raises(ValidationError, match="Skill steps must have sequential 0-based indices"):
        SkillDefinition(
            name="bad_order",
            description="Test",
            category="general",
            steps=steps_out_of_order,
            constraints=constraints,
            completion_criteria=criteria,
        )

    # Skipped index
    steps_skipped = [
        SkillStep(step_index=0, name="S0", description="D0", action_type="reasoning"),
        SkillStep(step_index=2, name="S2", description="D2", action_type="reasoning"),
    ]
    with pytest.raises(ValidationError, match="Skill steps must have sequential 0-based indices"):
        SkillDefinition(
            name="skipped_idx",
            description="Test",
            category="general",
            steps=steps_skipped,
            constraints=constraints,
            completion_criteria=criteria,
        )

    # Duplicate index
    steps_duplicate = [
        SkillStep(step_index=0, name="S0", description="D0", action_type="reasoning"),
        SkillStep(step_index=0, name="S0_dup", description="D0", action_type="reasoning"),
    ]
    with pytest.raises(ValidationError, match="Skill steps must have sequential 0-based indices"):
        SkillDefinition(
            name="duplicate_idx",
            description="Test",
            category="general",
            steps=steps_duplicate,
            constraints=constraints,
            completion_criteria=criteria,
        )
