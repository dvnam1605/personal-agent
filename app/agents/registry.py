"""First-party registry for agent capability declarations."""

from collections.abc import Iterable
from typing import overload

from app.domain.errors import ConfigurationError, NotFoundError, ValidationError
from app.domain.models import AgentDefinition

CapabilityList = list[str]
CapabilityCatalog = dict[str, CapabilityList]


class AgentRegistry:
    """Store immutable agent declarations without instantiating business agents."""

    def __init__(self, agents: Iterable[AgentDefinition] | None = None) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        if agents is not None:
            for agent in agents:
                self.register(agent)

    def register(self, agent: AgentDefinition) -> AgentDefinition:
        """Register one agent declaration and reject duplicate names."""
        if not isinstance(agent, AgentDefinition):
            raise ValidationError(
                "AgentRegistry.register expects an AgentDefinition instance.",
                details={"received_type": type(agent).__name__},
            )
        if agent.name in self._agents:
            raise ConfigurationError(
                f"Agent '{agent.name}' is already registered.",
                details={"agent_name": agent.name},
            )
        self._agents[agent.name] = agent.model_copy(deep=True)
        return agent.model_copy(deep=True)

    def get(self, agent_name: str) -> AgentDefinition:
        """Return a defensive copy of an agent declaration."""
        try:
            agent = self._agents[agent_name]
        except KeyError as exc:
            raise NotFoundError(
                f"Agent '{agent_name}' is not registered.",
                details={"agent_name": agent_name},
            ) from exc
        return agent.model_copy(deep=True)

    def list(self) -> list[AgentDefinition]:
        """List declarations in deterministic registration order."""
        return [agent.model_copy(deep=True) for agent in self._agents.values()]

    @overload
    def list_capabilities(self, agent_name: str) -> CapabilityList: ...

    @overload
    def list_capabilities(self, agent_name: None = None) -> CapabilityCatalog: ...

    def list_capabilities(
        self, agent_name: str | None = None
    ) -> CapabilityList | CapabilityCatalog:
        """List one agent's capabilities or a catalog for all registered agents."""
        if agent_name is not None:
            return list(self.get(agent_name).capabilities)
        return {name: list(agent.capabilities) for name, agent in self._agents.items()}

    def __contains__(self, agent_name: object) -> bool:
        return agent_name in self._agents

    def __len__(self) -> int:
        return len(self._agents)


__all__ = ["AgentRegistry"]
