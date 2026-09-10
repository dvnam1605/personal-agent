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
            ActionRiskLevel.HIGH_IMPACT_WRITE if is_mutation else ActionRiskLevel.READ_ONLY
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
    assert [tool.name for tool in registry.filter_by_capability("gmail.read")] == ["gmail.search"]


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


def test_tool_registry_restriction_allow_and_deny() -> None:
    """Verify ToolRegistry.restrict applies allow and deny constraints."""
    from app.domain.models import ToolRestriction

    registry = ToolRegistry(
        [
            _tool("gmail.search", capabilities=["gmail.read"]),
            _tool(
                "gmail.send",
                capabilities=["gmail.send"],
                is_mutation=True,
                action_class=ActionClass.EXTERNAL_COMMUNICATION,
            ),
            _tool("calendar.list", capabilities=["calendar.read"]),
            _tool(
                "calendar.create",
                capabilities=["calendar.write"],
                is_mutation=True,
                action_class=ActionClass.SAFE_WRITE,
            ),
        ]
    )

    # Allow gmail.* only
    scoped = registry.restrict(ToolRestriction(allow=["gmail.*"]))
    assert scoped.tool_names == ("gmail.search", "gmail.send")
    assert "calendar.list" in scoped.restricted_tool_names

    # Restricted tool rejected with PermissionDeniedError
    with pytest.raises(PermissionDeniedError, match="restricted"):
        scoped.get("calendar.list", agent_name="MockAgent")

    # Unknown tool raises NotFoundError
    with pytest.raises(NotFoundError):
        scoped.get("nonexistent.tool")

    # Deny mutations
    scoped_no_mutations = scoped.restrict(ToolRestriction(deny=["gmail.send"]))
    assert scoped_no_mutations.tool_names == ("gmail.search",)
    with pytest.raises(PermissionDeniedError):
        scoped_no_mutations.get("gmail.send", agent_name="MockAgent")


def test_future_mock_agents_scale_test() -> None:
    """Verify registry accepts mock future agents like TaskAgent and TravelAgent without architecture changes."""
    from app.agents import AgentRegistry
    from app.domain.enums import Domain
    from app.domain.models import AgentDefinition

    agent_registry = AgentRegistry()

    task_agent = AgentDefinition(
        name="TaskAgent",
        description="Mock future task management agent",
        domain=Domain.SYSTEM,
        capabilities=["task.create", "task.list"],
        allowed_tool_categories=["tasks"],
        delegation_allowed=True,
        max_child_depth=2,
    )
    travel_agent = AgentDefinition(
        name="TravelAgent",
        description="Mock future travel specialist agent",
        domain=Domain.GENERAL,
        capabilities=["flights.search", "hotels.book"],
        allowed_tool_categories=["travel"],
        delegation_allowed=False,
        max_child_depth=0,
    )

    agent_registry.register(task_agent)
    agent_registry.register(travel_agent)

    assert "TaskAgent" in agent_registry
    assert "TravelAgent" in agent_registry
    assert agent_registry.get("TaskAgent").delegation_allowed is True
    assert agent_registry.get("TravelAgent").delegation_allowed is False


def test_tool_output_spill_triggers_above_threshold_and_preserves_locator() -> None:
    """Verify tool output spill triggers above threshold and preserves locator in tool view execution."""
    from datetime import UTC, datetime

    from app.domain.models import ToolExecutionMetadata, ToolResult
    from app.domain.models.platform.spill import SpillPolicyConfig
    from app.services.platform.spill import InMemorySpillStore, SpillPolicy

    store = InMemorySpillStore()
    policy = SpillPolicy(store, SpillPolicyConfig(max_inline_bytes=256))

    large_output = "Line item " * 50  # ~500 bytes
    tool_result = ToolResult(
        tool_name="gmail.list_messages",
        success=True,
        output=large_output,
        metadata=ToolExecutionMetadata(
            tool_name="gmail.list_messages",
            latency_ms=10.0,
            timestamp=datetime.now(UTC),
        ),
    )

    processed = policy.process_tool_result(
        tool_result,
        session_id="session-user-1",
        call_id="call-99",
    )

    # Replaced output has preview and locator
    assert isinstance(processed.output, str)
    assert "spill://session-user-1/" in processed.output
    assert "Omitted" in processed.output

    # The persisted artifact exists in store and full output matches
    spills = store.list_spills("session-user-1")
    assert len(spills) == 1
    assert store.read_text(spills[0].locator) == large_output
