"""Unit tests for production SKILL.md documents and the declarative loader."""

from pathlib import Path

import pytest

from app.domain.errors import ValidationError
from app.services.skills.loader import parse_skill_directory, parse_skill_documents
from app.services.skills.registry import DEFAULT_SKILLS_DIR
from app.tools.google_calendar import CALENDAR_TOOL_DEFINITIONS
from app.tools.google_communication import COMMUNICATION_TOOL_DEFINITIONS
from app.tools.google_drive import DRIVE_TOOL_DEFINITIONS
from app.tools.knowledge import KNOWLEDGE_TOOL_DEFINITIONS

REGISTERED_TOOL_NAMES = {
    tool.name
    for tool in (
        *CALENDAR_TOOL_DEFINITIONS,
        *COMMUNICATION_TOOL_DEFINITIONS,
        *DRIVE_TOOL_DEFINITIONS,
        *KNOWLEDGE_TOOL_DEFINITIONS,
    )
}


def test_meeting_prep_skill_parses_six_ordered_steps() -> None:
    skill = parse_skill_directory(DEFAULT_SKILLS_DIR / "meeting-prep")
    assert skill.name == "meeting-prep"
    assert skill.version == "1.0.0"
    assert skill.metadata.is_mutation is False
    assert [step.step_index for step in skill.steps] == [1, 2, 3, 4, 5, 6]
    assert [step.name for step in skill.steps] == [
        "Find the target calendar event",
        "Extract participants and agenda topics",
        "Collect recent communications from participants",
        "Collect relevant documents and internal RAG context",
        "Identify unresolved action items and discussion points",
        "Synthesize a structured Meeting Brief",
    ]
    assert skill.steps[0].tool_name == "calendar.search_events"
    assert skill.steps[2].tool_name == "gmail.search_messages"
    assert skill.steps[3].tool_name == "retrieval.retrieve"
    for step in skill.steps:
        if step.tool_name is not None:
            assert step.tool_name in REGISTERED_TOOL_NAMES


def test_email_follow_up_skill_parses_four_ordered_steps() -> None:
    skill = parse_skill_directory(DEFAULT_SKILLS_DIR / "email-follow-up")
    assert skill.name == "email-follow-up"
    assert skill.metadata.is_mutation is True
    assert skill.constraints.allow_mutations is True
    assert [step.step_index for step in skill.steps] == [1, 2, 3, 4]
    assert skill.steps[0].tool_name == "gmail.get_thread"
    assert skill.steps[3].tool_name == "gmail.create_draft"
    for step in skill.steps:
        if step.tool_name is not None:
            assert step.tool_name in REGISTERED_TOOL_NAMES


def test_parse_skill_directory_requires_markdown(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="SKILL.md"):
        parse_skill_directory(tmp_path)


def test_frontmatter_and_nested_metadata_and_yaml_steps() -> None:
    markdown = "\n".join(
        [
            "---",
            "completion_criteria: Frontmatter completion.",
            "---",
            "",
            "# Body only",
        ]
    )
    skill = parse_skill_documents(
        markdown,
        yaml_payload={
            "metadata": {
                "name": "front-skill",
                "version": "1.0.0",
                "description": "Loaded from nested metadata mapping.",
            },
            "steps": [
                {
                    "step_index": 1,
                    "name": "Only",
                    "description": "YAML step",
                }
            ],
            "safety_constraints": None,
            "constraints": {"max_steps": 2},
        },
    )
    assert skill.name == "front-skill"
    assert skill.steps[0].name == "Only"
    assert skill.completion_criteria == "Frontmatter completion."


def test_loader_rejects_malformed_documents() -> None:
    with pytest.raises(ValidationError, match="completion_criteria"):
        parse_skill_documents(
            "## Step 1: One\n\nDesc\n",
            yaml_payload={
                "name": "no-complete",
                "version": "1.0.0",
                "description": "This description is long enough.",
            },
        )
    with pytest.raises(ValidationError, match="list"):
        parse_skill_documents(
            "## Step 1: One\n\nDesc\n",
            yaml_payload={
                "name": "bad-safety",
                "version": "1.0.0",
                "description": "This description is long enough.",
                "completion_criteria": "Done.",
                "safety_constraints": "not-a-list",
            },
        )
    with pytest.raises(ValidationError, match="mappings"):
        parse_skill_documents(
            "## Step 1: One\n\nDesc\n",
            yaml_payload={
                "name": "bad-schema",
                "version": "1.0.0",
                "description": "This description is long enough.",
                "completion_criteria": "Done.",
                "inputs_schema": ["nope"],
            },
        )
    with pytest.raises(ValidationError, match="must be a list"):
        parse_skill_documents(
            "# no markdown steps\n",
            yaml_payload={
                "name": "bad-steps",
                "version": "1.0.0",
                "description": "This description is long enough.",
                "completion_criteria": "Done.",
                "steps": {"step_index": 1},
            },
        )
    with pytest.raises(ValidationError, match="mapping"):
        parse_skill_documents(
            "# no markdown steps\n",
            yaml_payload={
                "name": "bad-step-item",
                "version": "1.0.0",
                "description": "This description is long enough.",
                "completion_criteria": "Done.",
                "steps": ["not-a-mapping"],
            },
        )


def test_invalid_yaml_and_invalid_constraints(tmp_path: Path) -> None:
    skill_dir = tmp_path / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("## Step 1: One\n\nDesc\n", encoding="utf-8")
    (skill_dir / "metadata.yaml").write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="YAML mapping"):
        parse_skill_directory(skill_dir)

    (skill_dir / "metadata.yaml").write_text("this: [unterminated\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="Invalid YAML"):
        parse_skill_directory(skill_dir)

    (skill_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "name: broken",
                "version: 1.0.0",
                "description: This description is long enough.",
                "completion_criteria: Done.",
                "constraints:",
                "  max_steps: 0",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        parse_skill_directory(skill_dir)


def test_invalid_step_payload_and_empty_yaml(tmp_path: Path) -> None:
    skill_dir = tmp_path / "empty-yaml"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: empty-yaml",
                "version: 1.0.0",
                "description: This description is long enough.",
                "completion_criteria: Done.",
                "---",
                "",
                "## Step 1: One",
                "",
                "- optional: false",
                "",
                "Desc",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "metadata.yaml").write_text("\n", encoding="utf-8")
    skill = parse_skill_directory(skill_dir)
    assert skill.steps[0].is_optional is False

    with pytest.raises(ValidationError):
        parse_skill_documents(
            "# none\n",
            yaml_payload={
                "name": "bad-step-model",
                "version": "1.0.0",
                "description": "This description is long enough.",
                "completion_criteria": "Done.",
                "steps": [{"step_index": 0, "name": "Zero", "description": "legacy"}],
            },
        )
    with pytest.raises(ValidationError, match="mutation flags"):
        parse_skill_documents(
            "## Step 1: One\n\nDesc\n",
            yaml_payload={
                "name": "flag-mismatch",
                "version": "1.0.0",
                "description": "This description is long enough.",
                "is_mutation": True,
                "completion_criteria": "Done.",
                "constraints": {"allow_mutations": False},
            },
        )
    inherited = parse_skill_documents(
        "## Step 1: One\n\nDesc\n",
        yaml_payload={
            "name": "flag-inherit",
            "version": "1.0.0",
            "description": "This description is long enough.",
            "is_mutation": True,
            "completion_criteria": "Done.",
            "constraints": {"max_steps": 2},
        },
    )
    assert inherited.constraints.allow_mutations is True


def test_markdown_steps_overlay_yaml_and_null_mapping() -> None:
    skill = parse_skill_documents(
        "## Step 1: From Markdown\n\nMarkdown body\n",
        yaml_payload={
            "name": "overlay-skill",
            "version": "1.0.0",
            "description": "This description is long enough.",
            "completion_criteria": "Done.",
            "steps": [
                {
                    "step_index": 1,
                    "name": "From YAML",
                    "description": "YAML body",
                    "tool_name": "gmail.get_thread",
                    "required_capability": "gmail.read",
                }
            ],
        },
    )
    assert skill.steps[0].name == "From Markdown"
    assert skill.steps[0].tool_name == "gmail.get_thread"
    assert skill.steps[0].required_capability == "gmail.read"

    with pytest.raises(ValidationError, match="missing from SKILL.md"):
        parse_skill_documents(
            "## Step 1: From Markdown\n\nMarkdown body\n",
            yaml_payload={
                "name": "yaml-only-step",
                "version": "1.0.0",
                "description": "This description is long enough.",
                "completion_criteria": "Done.",
                "steps": [
                    {
                        "step_index": 1,
                        "name": "From YAML",
                        "description": "YAML body",
                    },
                    {
                        "step_index": 3,
                        "name": "Orphan YAML step",
                        "description": "Should not be dropped silently.",
                    },
                ],
            },
        )

    skill_dir_text = parse_skill_documents(
        "---\nnull\n---\n\n## Step 1: One\n\nDesc\n",
        yaml_payload={
            "name": "null-frontmatter",
            "version": "1.0.0",
            "description": "This description is long enough.",
            "completion_criteria": "Done.",
        },
    )
    assert skill_dir_text.name == "null-frontmatter"
