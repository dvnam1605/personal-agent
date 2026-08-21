"""First-party tool metadata registry and immutable runtime views."""

from collections.abc import Callable, Iterable
from fnmatch import fnmatchcase
from typing import TypeAlias

import structlog

from app.domain.enums import ActionClass
from app.domain.errors import (
    ConfigurationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domain.errors import (
    ValidationError as DomainValidationError,
)
from app.domain.models import ToolDefinition, ToolRestriction

logger = structlog.get_logger(__name__)

MutationClassifier: TypeAlias = Callable[[ToolDefinition], ActionClass | str | None]
ToolDefinitions: TypeAlias = list[ToolDefinition]


def capability_matches(pattern: str, candidate: str) -> bool:
    """Match exact, hierarchical, or glob capability/category names."""
    normalized_pattern = pattern.strip().lower()
    normalized_candidate = candidate.strip().lower()
    if not normalized_pattern or not normalized_candidate:
        return False
    if normalized_pattern == "*":
        return True
    return (
        normalized_pattern == normalized_candidate
        or normalized_candidate.startswith(f"{normalized_pattern}.")
        or (normalized_pattern.endswith(".*") and normalized_candidate == normalized_pattern[:-2])
        or fnmatchcase(normalized_candidate, normalized_pattern)
    )


def _coerce_action_class(value: ActionClass | str) -> ActionClass:
    """Convert classifier output to the canonical enum with a domain error."""
    try:
        return value if isinstance(value, ActionClass) else ActionClass(value)
    except ValueError as exc:
        raise DomainValidationError(
            "Unknown mutation action class.",
            details={"action_class": str(value)},
        ) from exc


class ToolRegistry:
    """Registry of tool declarations with fail-closed mutation metadata checks."""

    def __init__(
        self,
        tools: Iterable[ToolDefinition] | None = None,
        *,
        mutation_classifier: MutationClassifier | None = None,
    ) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._mutation_classifier = mutation_classifier
        if tools is not None:
            for tool in tools:
                self.register(tool)

    @property
    def read_only(self) -> bool:
        """Full registries expose mutation tools and are not read-only views."""
        return False

    @property
    def is_read_only(self) -> bool:
        """Explicit alias used by runtime callers that inspect a view."""
        return self.read_only

    def register(self, tool: ToolDefinition) -> ToolDefinition:
        """Register one tool, rejecting duplicates and incomplete mutation metadata."""
        normalized = self._normalize_tool(tool)
        if normalized.name in self._tools:
            raise ConfigurationError(
                f"Tool '{normalized.name}' is already registered.",
                details={"tool_name": normalized.name},
            )
        self._tools[normalized.name] = normalized.model_copy(deep=True)
        return normalized.model_copy(deep=True)

    def get(self, tool_name: str) -> ToolDefinition:
        """Return a defensive copy of a registered tool or raise NOT_FOUND."""
        try:
            tool = self._tools[tool_name]
        except KeyError as exc:
            raise NotFoundError(
                f"Tool '{tool_name}' is not registered.",
                details={"tool_name": tool_name},
            ) from exc
        return tool.model_copy(deep=True)

    def list(self) -> list[ToolDefinition]:
        """List registered tools in deterministic registration order."""
        return [tool.model_copy(deep=True) for tool in self._tools.values()]

    def filter_by_capability(self, capability: str | Iterable[str]) -> ToolDefinitions:
        """Return tools matching any exact, hierarchical, or glob capability."""
        patterns = self._normalize_patterns(capability)
        return [
            tool.model_copy(deep=True)
            for tool in self._tools.values()
            if any(
                capability_matches(pattern, candidate)
                for pattern in patterns
                for candidate in tool.capability_names
            )
        ]

    def restrict(self, restriction: ToolRestriction) -> "ScopedToolView":
        """Create a restricted ScopedToolView applying allow/deny filters."""
        return _apply_tool_restriction(
            tools=self._tools.values(),
            restriction=restriction,
            is_read_only=False,
        )

    def as_read_only(self) -> "ToolRegistryView":
        """Create an immutable snapshot containing no mutation tools."""
        return ToolRegistryView(
            (tool for tool in self._tools.values() if not tool.is_mutation),
            is_read_only=True,
        )

    def read_only_view(self) -> "ToolRegistryView":
        """Named alias for callers that prefer an explicit view-oriented API."""
        return self.as_read_only()

    def _normalize_tool(self, tool: ToolDefinition) -> ToolDefinition:
        if not isinstance(tool, ToolDefinition):
            raise DomainValidationError(
                "ToolRegistry.register expects a ToolDefinition instance.",
                details={"received_type": type(tool).__name__},
            )

        if not tool.name.strip():
            raise DomainValidationError("A registered tool must have a non-blank name.")

        action_class = tool.action_class
        if tool.is_mutation:
            if action_class is None and self._mutation_classifier is not None:
                action_class = self._mutation_classifier(tool)
            if action_class is None:
                raise DomainValidationError(
                    f"Mutation tool '{tool.name}' must declare action_class.",
                    details={"tool_name": tool.name},
                )
            action_class = _coerce_action_class(action_class)
            if action_class == ActionClass.READ:
                raise DomainValidationError(
                    f"Mutation tool '{tool.name}' must not use action_class=READ.",
                    details={"tool_name": tool.name},
                )
        elif action_class is None:
            action_class = ActionClass.READ

        if action_class != tool.action_class:
            return tool.model_copy(update={"action_class": action_class})
        return tool

    def __contains__(self, tool_name: object) -> bool:
        return tool_name in self._tools

    def __iter__(self):
        yield from self.list()

    def __len__(self) -> int:
        return len(self._tools)

    @staticmethod
    def _normalize_patterns(capability: str | Iterable[str]) -> tuple[str, ...]:
        values = (capability,) if isinstance(capability, str) else tuple(capability)
        patterns = tuple(value.strip() for value in values if value.strip())
        if not patterns:
            raise DomainValidationError("At least one capability pattern is required.")
        return patterns


class ToolRegistryView:
    """Immutable snapshot of tools exposed to one runtime activation."""

    def __init__(self, tools: Iterable[ToolDefinition], *, is_read_only: bool) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._is_read_only = is_read_only
        for tool in tools:
            if is_read_only and tool.is_mutation:
                continue
            if tool.name in self._tools:
                raise ConfigurationError(
                    f"Tool '{tool.name}' is duplicated in the registry view.",
                    details={"tool_name": tool.name},
                )
            self._tools[tool.name] = tool.model_copy(deep=True)

    @property
    def read_only(self) -> bool:
        """Whether the view excludes all mutation tools."""
        return self._is_read_only

    @property
    def is_read_only(self) -> bool:
        """Explicit alias for security-sensitive callers."""
        return self.read_only

    @property
    def tool_names(self) -> tuple[str, ...]:
        """Return exposed names without exposing the mutable internal mapping."""
        return tuple(self._tools)

    def register(self, tool: ToolDefinition) -> None:
        """Prevent a view consumer from widening its own capability boundary."""
        del tool
        raise PermissionDeniedError("Tool registry views are immutable and cannot register tools.")

    def get(self, tool_name: str) -> ToolDefinition:
        """Return an exposed tool or behave as if an unavailable tool does not exist."""
        try:
            tool = self._tools[tool_name]
        except KeyError as exc:
            raise NotFoundError(
                f"Tool '{tool_name}' is not available in this registry view.",
                details={"tool_name": tool_name},
            ) from exc
        return tool.model_copy(deep=True)

    def list(self) -> list[ToolDefinition]:
        """List only the tools captured by this view."""
        return [tool.model_copy(deep=True) for tool in self._tools.values()]

    def filter_by_capability(self, capability: str | Iterable[str]) -> ToolDefinitions:
        """Filter only within the already-gated snapshot."""
        patterns = ToolRegistry._normalize_patterns(capability)
        return [
            tool.model_copy(deep=True)
            for tool in self._tools.values()
            if any(
                capability_matches(pattern, candidate)
                for pattern in patterns
                for candidate in tool.capability_names
            )
        ]

    def restrict(self, restriction: ToolRestriction) -> "ScopedToolView":
        """Create a restricted ScopedToolView applying allow/deny filters."""
        return _apply_tool_restriction(
            tools=self._tools.values(),
            restriction=restriction,
            is_read_only=self._is_read_only,
        )

    def as_read_only(self) -> "ToolRegistryView":
        """Narrow the current view to read-only tools without widening access."""
        return ToolRegistryView(
            (tool for tool in self._tools.values() if not tool.is_mutation),
            is_read_only=True,
        )

    def read_only_view(self) -> "ToolRegistryView":
        """Named alias matching ToolRegistry.read_only_view."""
        return self.as_read_only()

    def __contains__(self, tool_name: object) -> bool:
        return tool_name in self._tools

    def __iter__(self):
        yield from self.list()

    def __len__(self) -> int:
        return len(self._tools)


class ScopedToolView(ToolRegistryView):
    """Immutable tool view with active ToolRestriction constraints enforced."""

    def __init__(
        self,
        tools: Iterable[ToolDefinition],
        *,
        is_read_only: bool = False,
        restricted_tools: Iterable[ToolDefinition] | None = None,
    ) -> None:
        super().__init__(tools, is_read_only=is_read_only)
        self._restricted_tools: dict[str, ToolDefinition] = {
            t.name: t.model_copy(deep=True) for t in (restricted_tools or ())
        }

    @property
    def restricted_tool_names(self) -> tuple[str, ...]:
        """Names of tools known in the base scope but blocked by active restriction."""
        return tuple(self._restricted_tools)

    def get(self, tool_name: str, *, agent_name: str | None = None) -> ToolDefinition:
        """Return an exposed tool, or reject and log if tool is restricted."""
        if tool_name in self._restricted_tools:
            logger.warning(
                "Restricted tool invocation rejected by policy",
                tool_name=tool_name,
                agent_name=agent_name,
                reason="tool_restricted_by_policy",
            )
            raise PermissionDeniedError(
                f"Tool '{tool_name}' is restricted for agent '{agent_name or 'unknown'}'.",
                details={"tool_name": tool_name, "agent_name": agent_name},
            )
        return super().get(tool_name)

    def restrict(self, restriction: ToolRestriction) -> "ScopedToolView":
        """Apply an additional restriction to narrow this scoped view further."""
        return _apply_tool_restriction(
            tools=self._tools.values(),
            restriction=restriction,
            is_read_only=self._is_read_only,
            prior_restricted=self._restricted_tools.values(),
        )

    def as_read_only(self) -> "ScopedToolView":
        """Narrow this scoped view to read-only tools."""
        return ScopedToolView(
            (tool for tool in self._tools.values() if not tool.is_mutation),
            is_read_only=True,
            restricted_tools=self._restricted_tools.values(),
        )


def _apply_tool_restriction(
    tools: Iterable[ToolDefinition],
    restriction: ToolRestriction,
    *,
    is_read_only: bool = False,
    prior_restricted: Iterable[ToolDefinition] | None = None,
) -> ScopedToolView:
    """Evaluate ToolRestriction rules against tools and partition into visible vs restricted."""
    visible: list[ToolDefinition] = []
    restricted: dict[str, ToolDefinition] = {
        t.name: t.model_copy(deep=True) for t in (prior_restricted or ())
    }

    for tool in tools:
        is_allowed = True
        # If allow list is provided, tool must match at least one allow pattern
        if restriction.allow is not None:
            matches_allow = any(
                capability_matches(pattern, candidate)
                for pattern in restriction.allow
                for candidate in tool.capability_names
            )
            if not matches_allow:
                is_allowed = False

        # If deny list is provided, tool must not match any deny pattern
        if is_allowed and restriction.deny is not None:
            matches_deny = any(
                capability_matches(pattern, candidate)
                for pattern in restriction.deny
                for candidate in tool.capability_names
            )
            if matches_deny:
                is_allowed = False

        if is_allowed:
            visible.append(tool)
        else:
            restricted[tool.name] = tool.model_copy(deep=True)

    return ScopedToolView(
        visible,
        is_read_only=is_read_only,
        restricted_tools=restricted.values(),
    )


ReadOnlyToolRegistry = ToolRegistryView

__all__ = [
    "MutationClassifier",
    "ReadOnlyToolRegistry",
    "ScopedToolView",
    "ToolDefinitions",
    "ToolRegistry",
    "ToolRegistryView",
    "capability_matches",
]
