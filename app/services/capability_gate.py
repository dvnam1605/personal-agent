"""Runtime capability gate that constructs least-privilege tool snapshots."""

from collections.abc import Iterable

import structlog

from app.agents import AgentRegistry
from app.domain.models import AgentDefinition, ToolDefinition
from app.tools import ToolRegistry, ToolRegistryView, capability_matches

logger = structlog.get_logger(__name__)


class CapabilityGate:
    """Apply an agent declaration before tools are exposed to an activation."""

    def __init__(self, tool_registry: ToolRegistry, agent_registry: AgentRegistry) -> None:
        self._tool_registry = tool_registry
        self._agent_registry = agent_registry

    def for_agent(
        self,
        agent_name: str,
        *,
        read_only: bool = False,
        requested_capabilities: Iterable[str] | None = None,
    ) -> ToolRegistryView:
        """Build a snapshot limited by agent categories, capabilities, and mode.

        Category declarations are the primary exposure boundary. Explicit tool
        capability labels further narrow access when both sides provide them.
        An optional request-level capability list can narrow the result again,
        but it can never widen the agent declaration.
        """
        agent = self._agent_registry.get(agent_name)
        category_patterns = tuple(agent.allowed_tool_categories)
        requested_patterns = self._normalize_patterns(requested_capabilities)

        exposed: list[ToolDefinition] = []
        for tool in self._tool_registry.list():
            if read_only and tool.is_mutation:
                logger.info(
                    "capability_gate_rejected",
                    agent=agent_name,
                    tool=tool.name,
                    reason="read_only_strips_mutation",
                )
                continue
            if not self._category_allowed(tool, category_patterns):
                logger.info(
                    "capability_gate_rejected",
                    agent=agent_name,
                    tool=tool.name,
                    reason="category_not_declared",
                )
                continue
            if not self._declared_capability_allowed(tool, agent):
                logger.info(
                    "capability_gate_rejected",
                    agent=agent_name,
                    tool=tool.name,
                    reason="capability_not_declared",
                )
                continue
            if requested_patterns and not self._matches_any(tool, requested_patterns):
                logger.info(
                    "capability_gate_rejected",
                    agent=agent_name,
                    tool=tool.name,
                    reason="request_capability_mismatch",
                )
                continue
            exposed.append(tool)

        return ToolRegistryView(exposed, is_read_only=read_only)

    def build_view(
        self,
        agent_name: str,
        *,
        read_only: bool = False,
        requested_capabilities: Iterable[str] | None = None,
    ) -> ToolRegistryView:
        """Alias for callers that describe the result as a runtime view."""
        return self.for_agent(
            agent_name,
            read_only=read_only,
            requested_capabilities=requested_capabilities,
        )

    def read_only_view(
        self,
        agent_name: str,
        *,
        requested_capabilities: Iterable[str] | None = None,
    ) -> ToolRegistryView:
        """Build a view that cannot retrieve or register any mutation tool."""
        return self.for_agent(
            agent_name,
            read_only=True,
            requested_capabilities=requested_capabilities,
        )

    def build_read_only_view(
        self,
        agent_name: str,
        *,
        requested_capabilities: Iterable[str] | None = None,
    ) -> ToolRegistryView:
        """Explicit alias for read-only research execution callers."""
        return self.read_only_view(
            agent_name,
            requested_capabilities=requested_capabilities,
        )

    def can_access(self, agent_name: str, tool_name: str, *, read_only: bool = False) -> bool:
        """Return whether a tool is present in the gated view."""
        return tool_name in self.for_agent(agent_name, read_only=read_only)

    @staticmethod
    def _category_allowed(tool: ToolDefinition, patterns: tuple[str, ...]) -> bool:
        """Match a category against both a namespace and full tool name."""
        if not patterns:
            return False
        # An explicit category is authoritative. The name namespace is only a
        # fallback for legacy declarations that omit category metadata.
        candidates = (
            (tool.tool_category,) if tool.category is not None else (tool.tool_category, tool.name)
        )
        return any(
            capability_matches(pattern, candidate)
            for pattern in patterns
            for candidate in candidates
        )

    @staticmethod
    def _declared_capability_allowed(tool: ToolDefinition, agent: AgentDefinition) -> bool:
        """Use explicit tool labels as a second restriction when present."""
        if not tool.capabilities or not agent.capabilities:
            return True
        return CapabilityGate._matches_any(tool, tuple(agent.capabilities))

    @staticmethod
    def _matches_any(tool: ToolDefinition, patterns: tuple[str, ...]) -> bool:
        return any(
            capability_matches(pattern, candidate)
            for pattern in patterns
            for candidate in tool.capability_names
        )

    @staticmethod
    def _normalize_patterns(patterns: Iterable[str] | None) -> tuple[str, ...]:
        if patterns is None:
            return ()
        return tuple(value.strip() for value in patterns if value.strip())


__all__ = ["CapabilityGate"]
