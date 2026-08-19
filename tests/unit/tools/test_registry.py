"""Unit tests for P4 tool registry and mutation classification."""

import pytest

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.errors import (
    ConfigurationError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.models import ToolDefinition
from app.tools import ToolRegistry, capability_matches


def _tool(
    name: str,
    *,
    category: str | None = None,
    capabilities: list[str] | None = None,
    is_mutation: bool = False,
    action_class: ActionClass | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Test tool {name}",
        category=category,
        capabilities=capabilities or [],
        risk_level=(
            ActionRiskLevel.HIGH_IMPACT_WRITE
            if is_mutation
            else ActionRiskLevel.READ_ONLY
        ),
        is_mutation=is_mutation,
        action_class=action_class,
    )


def test_duplicate_tool_registration_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(_tool("gmail.search"))

    with pytest.raises(ConfigurationError, match="already registered"):
        registry.register(_tool("gmail.search"))


def test_filter_by_capability_supports_namespace_and_fine_grained_labels() -> None:
    registry = ToolRegistry(
        [
            _tool("gmail.search", capabilities=["gmail.read"]),
            _tool("gmail.send", capabilities=["gmail.send"]),
            _tool("calendar.list", capabilities=["calendar.read"]),
        ]
    )

    assert [tool.name for tool in registry.filter_by_capability("gmail")] == [
        "gmail.search",
        "gmail.send",
    ]
    assert [tool.name for tool in registry.filter_by_capability("gmail.read")] == [
        "gmail.search"
    ]


def test_tool_identity_and_capability_labels_are_canonicalized() -> None:
    tool = ToolDefinition(
        name=" gmail.search ",
        description=" Search Gmail ",
        category=" gmail ",
        capabilities=[" gmail.read ", "gmail.read"],
    )

    assert tool.name == "gmail.search"
    assert tool.description == "Search Gmail"
    assert tool.category == "gmail"
    assert tool.capabilities == ["gmail.read"]


def test_literal_all_is_not_an_implicit_wildcard() -> None:
    assert capability_matches("all", "all") is True
    assert capability_matches("all", "drive.delete") is False
    assert capability_matches("*", "drive.delete") is True


def test_mutation_classifier_hook_completes_registration_metadata() -> None:
    registry = ToolRegistry(
        mutation_classifier=lambda _: ActionClass.SAFE_WRITE,
    )

    registered = registry.register(_tool("gmail.archive", is_mutation=True))

    assert registered.action_class == ActionClass.SAFE_WRITE


def test_mutation_requires_non_read_action_class_at_registration() -> None:
    registry = ToolRegistry()

    with pytest.raises(ValidationError, match="must declare action_class"):
        registry.register(_tool("gmail.send", is_mutation=True))

    registered = registry.register(
        _tool(
            "gmail.send",
            is_mutation=True,
            action_class=ActionClass.EXTERNAL_COMMUNICATION,
        )
    )
    assert registered.action_class == ActionClass.EXTERNAL_COMMUNICATION


def test_read_only_view_removes_mutations_and_cannot_be_widened() -> None:
    registry = ToolRegistry(
        [
            _tool("gmail.search"),
            _tool(
                "gmail.send",
                is_mutation=True,
                action_class=ActionClass.EXTERNAL_COMMUNICATION,
            ),
        ]
    )

    view = registry.as_read_only()
    assert view.read_only is True
    assert view.tool_names == ("gmail.search",)
    with pytest.raises(NotFoundError):
        view.get("gmail.send")
    with pytest.raises(PermissionDeniedError):
        view.register(_tool("calendar.list"))


def test_read_only_view_is_a_snapshot() -> None:
    registry = ToolRegistry([_tool("gmail.search")])
    view = registry.as_read_only()

    registry.register(_tool("calendar.list"))

    assert view.tool_names == ("gmail.search",)
    assert [tool.name for tool in registry.list()] == ["gmail.search", "calendar.list"]


def test_registry_returns_defensive_copies() -> None:
    registry = ToolRegistry([_tool("gmail.search", capabilities=["gmail.read"])])

    returned = registry.get("gmail.search")
    returned.capabilities.append("dangerous.local_mutation")

    assert registry.get("gmail.search").capabilities == ["gmail.read"]
