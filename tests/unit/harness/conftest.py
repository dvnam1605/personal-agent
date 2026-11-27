"""Shared fixtures for the harness tests: the first-party agent registry, its capability catalog and a base assistant state."""

from __future__ import annotations

import pytest

from app.agents.declarations import build_first_party_registry
from app.agents.registry import AgentRegistry
from app.domain.models import AssistantState
from app.domain.models.supervisor import CapabilityCatalog
from app.services.supervisor.catalog import build_capability_catalog


@pytest.fixture
def default_registry() -> AgentRegistry:
    return build_first_party_registry()


@pytest.fixture
def default_catalog(default_registry: AgentRegistry) -> CapabilityCatalog:
    return build_capability_catalog(default_registry)


@pytest.fixture
def base_state() -> AssistantState:
    return AssistantState(
        user_id="user_test_p16",
        request="Test request for supervisor",
        goal="Complete supervisor test suite",
    )
