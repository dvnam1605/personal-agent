"""Skill discovery, versioning, validation, and intent-based matching."""

from __future__ import annotations

from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.domain.errors import ConfigurationError, ValidationError
from app.domain.models import SkillDefinition, SkillMetadata
from app.services.skills.loader import (
    ALLOWED_SKILL_FILES,
    SKILL_MARKDOWN_NAME,
    parse_skill_directory,
)
from app.services.skills.matching import capabilities_are_satisfied, match_trigger

DEFAULT_SKILLS_DIR = PROJECT_ROOT / "skills"


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a ``MAJOR.MINOR.PATCH`` string into a comparable tuple."""
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


class SkillRegistry:
    """Manages skill discovery, validation, and intent-based matching."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills: dict[tuple[str, str], SkillDefinition] = {}
        if skills_dir is not None:
            self.load_from_directory(skills_dir)

    def register(self, skill: SkillDefinition) -> None:
        """Register one immutable skill version and reject duplicates."""
        if not isinstance(skill, SkillDefinition):
            raise ValidationError(
                "SkillRegistry.register expects a SkillDefinition instance.",
                details={"received_type": type(skill).__name__},
            )
        key = (skill.metadata.name, skill.metadata.version)
        if key in self._skills:
            raise ConfigurationError(
                f"Skill '{skill.metadata.name}' version '{skill.metadata.version}' "
                "is already registered.",
                details={"name": skill.metadata.name, "version": skill.metadata.version},
            )
        self._skills[key] = skill.model_copy(deep=True)

    def get(self, name: str, version: str | None = None) -> SkillDefinition | None:
        """Return a defensive copy of one skill, defaulting to the latest version."""
        normalized = name.strip()
        if not normalized:
            return None
        if version is not None:
            skill = self._skills.get((normalized, version.strip()))
            return skill.model_copy(deep=True) if skill is not None else None
        versions = [
            skill for (skill_name, _), skill in self._skills.items() if skill_name == normalized
        ]
        if not versions:
            return None
        latest = max(versions, key=lambda item: parse_semver(item.metadata.version))
        return latest.model_copy(deep=True)

    def match(
        self, intent_or_query: str, available_capabilities: set[str]
    ) -> list[SkillDefinition]:
        """Return skills whose triggers hit and whose capabilities the caller holds."""
        query = intent_or_query.strip()
        if not query:
            return []
        matches: list[SkillDefinition] = []
        for skill in self._iter_latest():
            if not any(
                match_trigger(trigger, query, allow_token_subset=True)
                for trigger in skill.metadata.triggers
            ):
                continue
            if not capabilities_are_satisfied(
                skill.metadata.required_capabilities, available_capabilities
            ):
                continue
            matches.append(skill.model_copy(deep=True))
        matches.sort(key=lambda item: item.metadata.name)
        return matches

    def load_from_directory(self, directory: Path) -> int:
        """Load every skill subdirectory that contains ``SKILL.md``."""
        root = Path(directory)
        if not root.is_dir():
            root.mkdir(parents=True, exist_ok=True)
            return 0
        loaded = 0
        for child in sorted(path for path in root.iterdir() if path.is_dir()):
            markdown = child / SKILL_MARKDOWN_NAME
            if not markdown.is_file():
                continue
            unexpected = [
                path.name
                for path in child.iterdir()
                if path.is_file()
                and path.name not in ALLOWED_SKILL_FILES
                and not path.name.startswith(".")
            ]
            if any(name.endswith((".py", ".pyc", ".pyo")) for name in unexpected):
                raise ValidationError(
                    "Skill directories cannot contain executable Python files.",
                    details={"directory": str(child), "files": unexpected},
                )
            self.register(parse_skill_directory(child))
            loaded += 1
        return loaded

    def list_all(self) -> list[SkillMetadata]:
        """List metadata for every registered name/version, sorted stably."""
        skills = sorted(
            self._skills.values(),
            key=lambda skill: (skill.metadata.name, parse_semver(skill.metadata.version)),
        )
        return [skill.metadata.model_copy(deep=True) for skill in skills]

    def _iter_latest(self) -> list[SkillDefinition]:
        latest: dict[str, SkillDefinition] = {}
        for skill in self._skills.values():
            current = latest.get(skill.metadata.name)
            if current is None or parse_semver(skill.metadata.version) > parse_semver(
                current.metadata.version
            ):
                latest[skill.metadata.name] = skill
        return list(latest.values())


def load_production_skills(skills_dir: Path | None = None) -> SkillRegistry:
    """Build a registry from the repository ``skills/`` tree."""
    return SkillRegistry(skills_dir or DEFAULT_SKILLS_DIR)
