"""Parse declarative skill documents (markdown + YAML). Never evaluates code."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import ValidationError
from app.domain.models import SkillConstraints, SkillDefinition, SkillMetadata, SkillStep

FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<yaml>.*?)\n---\s*(?:\n(?P<body>.*))?\Z", re.DOTALL)
STEP_HEADING_RE = re.compile(
    r"^##\s+Step\s+(?P<index>\d+)\s*[:.\-—]\s*(?P<name>.+?)\s*$",
    re.MULTILINE,
)
TOOL_LINE_RE = re.compile(r"^[-*]\s*tool\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
CAPABILITY_LINE_RE = re.compile(r"^[-*]\s*capability\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
OUTPUTS_LINE_RE = re.compile(r"^[-*]\s*outputs?\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
OPTIONAL_LINE_RE = re.compile(
    r"^[-*]\s*optional\s*:\s*(?P<value>true|false|yes|no)\s*$",
    re.IGNORECASE,
)

SKILL_MARKDOWN_NAME = "SKILL.md"
METADATA_YAML_NAME = "metadata.yaml"
ALLOWED_SKILL_FILES = frozenset({SKILL_MARKDOWN_NAME, METADATA_YAML_NAME})
_METADATA_KEYS = frozenset(
    {
        "name",
        "version",
        "description",
        "required_capabilities",
        "tags",
        "triggers",
        "is_mutation",
    }
)


def parse_skill_directory(directory: Path) -> SkillDefinition:
    """Load one skill folder of ``SKILL.md`` plus optional ``metadata.yaml``."""
    skill_dir = Path(directory)
    markdown_path = skill_dir / SKILL_MARKDOWN_NAME
    if not markdown_path.is_file():
        raise ValidationError(
            "Skill directory must contain SKILL.md.",
            details={"directory": str(skill_dir)},
        )
    markdown_text = markdown_path.read_text(encoding="utf-8")
    yaml_payload: dict[str, Any] = {}
    metadata_path = skill_dir / METADATA_YAML_NAME
    if metadata_path.is_file():
        yaml_payload = _load_yaml_mapping(
            metadata_path.read_text(encoding="utf-8"),
            source=str(metadata_path),
        )
    return parse_skill_documents(markdown_text, yaml_payload=yaml_payload)


def parse_skill_documents(
    markdown_text: str,
    *,
    yaml_payload: dict[str, Any] | None = None,
) -> SkillDefinition:
    """Build a ``SkillDefinition`` from markdown guidance and YAML metadata."""
    body, frontmatter = _split_frontmatter(markdown_text)
    merged: dict[str, Any] = {}
    merged.update(frontmatter)
    merged.update(yaml_payload or {})

    yaml_steps = _coerce_step_payloads(merged.pop("steps", None))
    markdown_steps = _parse_markdown_steps(body)
    steps = _merge_steps(markdown_steps, yaml_steps)
    if not steps:
        raise ValidationError("Skill definition must declare at least one step.")

    try:
        metadata = _extract_metadata(merged)
    except PydanticValidationError as exc:
        raise ValidationError(str(exc)) from exc

    constraints_payload = merged.get("constraints")
    try:
        constraints = (
            SkillConstraints.model_validate(constraints_payload)
            if isinstance(constraints_payload, dict)
            else SkillConstraints()
        )
    except PydanticValidationError as exc:
        raise ValidationError(str(exc)) from exc
    completion = merged.get("completion_criteria")
    if not isinstance(completion, str) or not completion.strip():
        raise ValidationError("Skill completion_criteria must be a non-blank string.")
    safety = merged.get("safety_constraints", [])
    if safety is None:
        safety = []
    if not isinstance(safety, list):
        raise ValidationError("Skill safety_constraints must be a list of strings.")

    inputs_schema = merged.get("inputs_schema") or {}
    output_schema = merged.get("output_schema") or {}
    if not isinstance(inputs_schema, dict) or not isinstance(output_schema, dict):
        raise ValidationError("Skill input/output schemas must be mappings.")

    try:
        return SkillDefinition(
            metadata=metadata,
            inputs_schema=inputs_schema,
            steps=steps,
            output_schema=output_schema,
            safety_constraints=[str(item) for item in safety],
            completion_criteria=completion,
            guidance=body.strip(),
            constraints=constraints,
        )
    except (PydanticValidationError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc


def _extract_metadata(merged: dict[str, Any]) -> SkillMetadata:
    nested = merged.pop("metadata", None)
    if isinstance(nested, dict):
        return SkillMetadata.model_validate(nested)
    payload = {key: merged.pop(key) for key in list(merged) if key in _METADATA_KEYS}
    return SkillMetadata.model_validate(payload)


def _split_frontmatter(markdown_text: str) -> tuple[str, dict[str, Any]]:
    match = FRONTMATTER_RE.match(markdown_text.strip())
    if not match:
        return markdown_text, {}
    payload = _load_yaml_mapping(match.group("yaml"), source="SKILL.md frontmatter")
    return (match.group("body") or ""), payload


def _load_yaml_mapping(text: str, *, source: str) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"Invalid YAML in {source}.", details={"source": source}) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValidationError(
            f"{source} must contain a YAML mapping.",
            details={"source": source},
        )
    return loaded


def _coerce_step_payloads(raw: object) -> list[SkillStep]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValidationError("Skill steps must be a list.")
    steps: list[SkillStep] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValidationError("Each skill step must be a mapping.")
        try:
            steps.append(SkillStep.model_validate(item))
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc
    return steps


def _parse_markdown_steps(markdown_text: str) -> list[SkillStep]:
    matches = list(STEP_HEADING_RE.finditer(markdown_text))
    if not matches:
        return []
    steps: list[SkillStep] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown_text)
        body = markdown_text[start:end].strip()
        tool_name = _first_match(TOOL_LINE_RE, body)
        capability = _first_match(CAPABILITY_LINE_RE, body)
        outputs_raw = _first_match(OUTPUTS_LINE_RE, body)
        optional_raw = _first_match(OPTIONAL_LINE_RE, body)
        expected_outputs = (
            [part.strip() for part in outputs_raw.split(",") if part.strip()] if outputs_raw else []
        )
        steps.append(
            SkillStep(
                step_index=int(match.group("index")),
                name=match.group("name").strip(),
                description=body or match.group("name").strip(),
                required_capability=capability,
                expected_outputs=expected_outputs,
                is_optional=_parse_optional_flag(optional_raw),
                tool_name=tool_name,
            )
        )
    return steps


def _merge_steps(markdown_steps: list[SkillStep], yaml_steps: list[SkillStep]) -> list[SkillStep]:
    if not markdown_steps:
        return yaml_steps
    yaml_by_index = {step.step_index: step for step in yaml_steps}
    markdown_indices = {step.step_index for step in markdown_steps}
    yaml_only = sorted(set(yaml_by_index) - markdown_indices)
    if yaml_only:
        raise ValidationError(
            "Skill YAML steps include indices that are missing from SKILL.md.",
            details={"yaml_only_step_indices": yaml_only},
        )
    merged: list[SkillStep] = []
    for step in markdown_steps:
        overlay = yaml_by_index.get(step.step_index)
        if overlay is None:
            merged.append(step)
            continue
        merged.append(
            step.model_copy(
                update={
                    "required_capability": step.required_capability or overlay.required_capability,
                    "expected_outputs": step.expected_outputs or overlay.expected_outputs,
                    "tool_name": step.tool_name or overlay.tool_name,
                    "is_optional": step.is_optional or overlay.is_optional,
                }
            )
        )
    return merged


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group("value").strip()
    return None


def _parse_optional_flag(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().casefold() in {"true", "yes"}
