"""Unit tests for SkillRegistry discovery, versioning, and intent matching."""

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import ConfigurationError, ValidationError
from app.domain.models import SkillConstraints, SkillDefinition, SkillMetadata, SkillStep
from app.services.skills.matching import capability_is_satisfied, trigger_matches
from app.services.skills.registry import SkillRegistry, load_production_skills


def _skill(
    name: str,
    *,
    version: str = "1.0.0",
    triggers: list[str] | None = None,
    required: list[str] | None = None,
    is_mutation: bool = False,
) -> SkillDefinition:
    return SkillDefinition(
        metadata=SkillMetadata(
            name=name,
            version=version,
            description="Reusable procedure used by unit tests only.",
            required_capabilities=required or ["gmail.read"],
            triggers=triggers or [name.replace("-", " ")],
            is_mutation=is_mutation,
        ),
        steps=[
            SkillStep(step_index=1, name="Do work", description="Follow the procedure."),
        ],
        completion_criteria="The procedure completed or reported a gap.",
        constraints=SkillConstraints(max_steps=3, allow_mutations=is_mutation),
    )


def test_register_rejects_invalid_version_at_the_model_boundary() -> None:
    with pytest.raises(PydanticValidationError, match="version"):
        SkillMetadata(
            name="bad-version",
            version="v1",
            description="This description is long enough.",
        )


def test_register_and_get_latest_version() -> None:
    registry = SkillRegistry()
    registry.register(_skill("meeting-prep", version="1.0.0"))
    registry.register(_skill("meeting-prep", version="1.2.0"))
    registry.register(_skill("meeting-prep", version="1.1.0"))

    latest = registry.get("meeting-prep")
    assert latest is not None
    assert latest.version == "1.2.0"
    pinned = registry.get("meeting-prep", "1.0.0")
    assert pinned is not None
    assert pinned.version == "1.0.0"
    assert registry.get("missing") is None


def test_duplicate_name_and_version_is_rejected() -> None:
    registry = SkillRegistry()
    registry.register(_skill("meeting-prep"))
    with pytest.raises(ConfigurationError, match="already registered"):
        registry.register(_skill("meeting-prep"))


def test_match_filters_on_triggers_and_caller_capabilities() -> None:
    registry = SkillRegistry()
    registry.register(
        _skill(
            "meeting-prep",
            triggers=["meeting prep", "chuẩn bị họp"],
            required=["calendar.read", "gmail.read", "retrieval.read"],
        )
    )
    registry.register(
        _skill(
            "email-follow-up",
            triggers=["email follow-up"],
            required=["gmail.read", "gmail.drafts"],
            is_mutation=True,
        )
    )

    owned = {"calendar.read", "gmail.read", "retrieval.read", "drive.read"}
    hits = registry.match("Please run meeting prep for tomorrow", owned)
    assert [skill.name for skill in hits] == ["meeting-prep"]

    denied = registry.match("meeting prep", {"gmail.read"})
    assert denied == []

    other = registry.match("email follow-up please", owned)
    assert other == []


def test_match_ignores_blank_queries() -> None:
    registry = SkillRegistry()
    registry.register(_skill("meeting-prep", triggers=["meeting prep"]))
    assert registry.match("   ", {"gmail.read"}) == []


def test_trigger_regex_and_invalid_pattern() -> None:
    assert trigger_matches("/follow-?up/", "Please follow-up with Nam") is True
    assert trigger_matches("/(unclosed/", "follow-up") is False
    assert trigger_matches("chuẩn bị họp", "Cần CHUẨN BỊ HỌP gấp") is True
    assert trigger_matches("chuẩn bị họp", "chuan bi hop ngay mai") is True
    assert trigger_matches("chuẩn bị họp", "chuan bi cuoc hop mai") is True
    assert trigger_matches("soạn follow-up", "Hay soan follow-up giup toi") is True
    assert trigger_matches("điểm danh", "diem danh hop") is True
    assert trigger_matches("!!!", "hello") is False
    assert trigger_matches("\u0301", "hello") is False
    assert trigger_matches("   ", "query") is False
    assert capability_is_satisfied("  ", {"gmail.read"}) is False


def test_match_unaccented_vietnamese_query() -> None:
    registry = SkillRegistry()
    registry.register(
        _skill(
            "meeting-prep",
            triggers=["chuẩn bị họp"],
            required=["calendar.read", "gmail.read"],
        )
    )
    hits = registry.match("chuan bi hop cho ngay mai", {"calendar.read", "gmail.read"})
    assert [skill.name for skill in hits] == ["meeting-prep"]


def test_register_rejects_non_skill_definitions() -> None:
    registry = SkillRegistry()
    with pytest.raises(ValidationError, match="SkillDefinition"):
        registry.register("not-a-skill")  # type: ignore[arg-type]
    assert registry.get("   ") is None


def test_load_from_directory_skips_incomplete_folders(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "README.md").write_text("ignore me\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="does not exist"):
        SkillRegistry().load_from_directory(tmp_path / "missing")
    assert SkillRegistry().load_from_directory(tmp_path) == 0


def test_load_from_directory_rejects_invalid_version(tmp_path: Path) -> None:
    skill_dir = tmp_path / "bad-version"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("## Step 1: One\n\nDo the thing.\n", encoding="utf-8")
    (skill_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "name: bad-version",
                "version: '1.0'",
                "description: This description is long enough.",
                "completion_criteria: Done.",
            ]
        ),
        encoding="utf-8",
    )
    registry = SkillRegistry()
    with pytest.raises(ValidationError, match="version"):
        registry.load_from_directory(tmp_path)


def test_load_from_directory_rejects_python_payloads(tmp_path: Path) -> None:
    skill_dir = tmp_path / "evil-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "## Step 1: Run code\n\n- tool: gmail.get_thread\n",
        encoding="utf-8",
    )
    (skill_dir / "hook.py").write_text("print('nope')\n", encoding="utf-8")
    registry = SkillRegistry()
    with pytest.raises(ValidationError, match="executable Python"):
        registry.load_from_directory(tmp_path)


def test_load_from_directory_missing_steps_is_rejected(tmp_path: Path) -> None:
    skill_dir = tmp_path / "empty-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# No steps here\n", encoding="utf-8")
    (skill_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "name: empty-skill",
                "version: 1.0.0",
                "description: This description is long enough.",
                "completion_criteria: Done.",
            ]
        ),
        encoding="utf-8",
    )
    registry = SkillRegistry()
    with pytest.raises(ValidationError, match="at least one step"):
        registry.load_from_directory(tmp_path)


def test_procedure_can_change_without_runtime_code_changes(tmp_path: Path) -> None:
    skill_dir = tmp_path / "dynamic-skill"
    skill_dir.mkdir()
    (skill_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "name: dynamic-skill",
                "version: 1.0.0",
                "description: Procedure edited without changing runtime code.",
                "required_capabilities: [gmail.read]",
                "completion_criteria: Brief produced.",
                "constraints:",
                "  max_steps: 5",
                "  allow_mutations: false",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "## Step 1: One\n\nFirst.\n\n## Step 2: Two\n\nSecond.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    assert registry.load_from_directory(tmp_path) == 1
    first = registry.get("dynamic-skill")
    assert first is not None
    assert [step.name for step in first.steps] == ["One", "Two"]

    (skill_dir / "SKILL.md").write_text(
        "## Step 1: One\n\nFirst.\n\n## Step 2: Two\n\nSecond.\n\n## Step 3: Three\n\nThird.\n",
        encoding="utf-8",
    )
    reloaded = SkillRegistry()
    reloaded.load_from_directory(tmp_path)
    updated = reloaded.get("dynamic-skill")
    assert updated is not None
    assert [step.name for step in updated.steps] == ["One", "Two", "Three"]


def test_production_skills_load_from_repository() -> None:
    registry = load_production_skills()
    names = {item.name for item in registry.list_all()}
    assert names == {"email-follow-up", "meeting-prep"}
    assert registry.get("meeting-prep") is not None
    assert registry.get("email-follow-up") is not None
