"""Capability catalog builder for Supervisor planning (spec P16-02 / ADR 0011).

Extracts a high-level, capability-only view from AgentRegistry.
Low-level tool names and schemas are strictly hidden from the Supervisor.
"""

from __future__ import annotations

from app.agents.registry import AgentRegistry
from app.domain.models.supervisor import AgentCapabilityDescriptor, CapabilityCatalog


def build_capability_catalog(registry: AgentRegistry) -> CapabilityCatalog:
    """Build a read-only CapabilityCatalog from the AgentRegistry.

    Enforces P16-02: Supervisor sees agent capabilities only, never raw tool definitions.
    """
    descriptors: list[AgentCapabilityDescriptor] = []
    for agent in registry.list():
        descriptors.append(
            AgentCapabilityDescriptor(
                agent_name=agent.name,
                domain=agent.domain,
                description=agent.description,
                capabilities=list(agent.capabilities),
                max_child_depth=agent.max_child_depth,
            )
        )
    return CapabilityCatalog(agents=descriptors)
