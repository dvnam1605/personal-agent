"""Unit tests for RouteDecision model and semantic invariants."""

import pytest
from pydantic import ValidationError

from app.domain.enums import Complexity, Domain, RouteType
from app.domain.models import RouteDecision


def test_valid_direct_specialist_route() -> None:
    """Verify DIRECT_SPECIALIST route with exactly 1 domain and no workflow name."""
    route = RouteDecision(
        domains=[Domain.COMMUNICATION],
        complexity=Complexity.ADAPTIVE,
        route_type=RouteType.DIRECT_SPECIALIST,
        workflow_name=None,
        confidence=0.95,
        reason_code="SINGLE_SPECIALIST_COMM",
    )
    assert route.domains == [Domain.COMMUNICATION]
    assert route.complexity == Complexity.ADAPTIVE
    assert route.route_type == RouteType.DIRECT_SPECIALIST
    assert route.workflow_name is None
    assert route.confidence == 0.95


def test_valid_known_workflow_route() -> None:
    """Verify KNOWN_WORKFLOW route with multi-domain and workflow_name."""
    route = RouteDecision(
        domains=[Domain.COMMUNICATION, Domain.CALENDAR],
        complexity=Complexity.MULTI_STEP,
        route_type=RouteType.KNOWN_WORKFLOW,
        workflow_name="meeting_prep_hardened",
        confidence=0.90,
    )
    assert route.route_type == RouteType.KNOWN_WORKFLOW
    assert route.workflow_name == "meeting_prep_hardened"


def test_valid_casual_route() -> None:
    """Verify CASUAL_RESPONSE route with GENERAL domain or empty."""
    route = RouteDecision(
        domains=[Domain.GENERAL],
        complexity=Complexity.DIRECT,
        route_type=RouteType.CASUAL_RESPONSE,
        confidence=0.99,
    )
    assert route.route_type == RouteType.CASUAL_RESPONSE


def test_known_workflow_missing_workflow_name_rejected() -> None:
    """Verify KNOWN_WORKFLOW without workflow_name is rejected."""
    with pytest.raises(ValidationError, match="workflow_name is required"):
        RouteDecision(
            domains=[Domain.COMMUNICATION, Domain.CALENDAR],
            complexity=Complexity.MULTI_STEP,
            route_type=RouteType.KNOWN_WORKFLOW,
            workflow_name=None,
            confidence=0.85,
        )

    with pytest.raises(ValidationError, match="workflow_name is required"):
        RouteDecision(
            domains=[Domain.COMMUNICATION],
            complexity=Complexity.MULTI_STEP,
            route_type=RouteType.KNOWN_WORKFLOW,
            workflow_name="   ",
            confidence=0.85,
        )


def test_non_workflow_with_workflow_name_rejected() -> None:
    """Verify non-workflow route cannot have a workflow_name."""
    with pytest.raises(ValidationError, match="workflow_name must be None"):
        RouteDecision(
            domains=[Domain.COMMUNICATION],
            complexity=Complexity.DIRECT,
            route_type=RouteType.DIRECT_SPECIALIST,
            workflow_name="illegal_workflow",
            confidence=0.9,
        )


def test_direct_specialist_multi_domain_rejected() -> None:
    """Verify DIRECT_SPECIALIST cannot have multiple domains."""
    with pytest.raises(ValidationError, match="requires exactly 1 domain"):
        RouteDecision(
            domains=[Domain.COMMUNICATION, Domain.CALENDAR],
            complexity=Complexity.ADAPTIVE,
            route_type=RouteType.DIRECT_SPECIALIST,
            confidence=0.9,
        )


def test_empty_domains_rejected() -> None:
    """Verify route requires at least one domain."""
    with pytest.raises(ValidationError):
        RouteDecision(
            domains=[],
            complexity=Complexity.DIRECT,
            route_type=RouteType.CASUAL_RESPONSE,
            confidence=0.9,
        )


def test_casual_route_requires_general_domain() -> None:
    """Verify CASUAL_RESPONSE requires exactly [Domain.GENERAL]."""
    with pytest.raises(ValidationError, match="requires exactly"):
        RouteDecision(
            domains=[Domain.CALENDAR],
            complexity=Complexity.DIRECT,
            route_type=RouteType.CASUAL_RESPONSE,
            confidence=0.9,
        )

    with pytest.raises(ValidationError, match="requires exactly"):
        RouteDecision(
            domains=[Domain.GENERAL, Domain.COMMUNICATION],
            complexity=Complexity.DIRECT,
            route_type=RouteType.CASUAL_RESPONSE,
            confidence=0.9,
        )


def test_route_decision_confidence_bounds() -> None:
    """Verify confidence must be between 0.0 and 1.0."""
    with pytest.raises(ValidationError):
        RouteDecision(
            domains=[Domain.GENERAL],
            complexity=Complexity.DIRECT,
            route_type=RouteType.CASUAL_RESPONSE,
            confidence=1.5,
        )

    with pytest.raises(ValidationError):
        RouteDecision(
            domains=[Domain.GENERAL],
            complexity=Complexity.DIRECT,
            route_type=RouteType.CASUAL_RESPONSE,
            confidence=-0.1,
        )


def test_route_decision_json_roundtrip() -> None:
    """Verify serialization and deserialization."""
    route = RouteDecision(
        domains=[Domain.KNOWLEDGE_RESEARCH],
        complexity=Complexity.MULTI_STEP,
        route_type=RouteType.KNOWN_WORKFLOW,
        workflow_name="deep_research",
        confidence=0.88,
        reason_code="WORKFLOW_MATCH",
    )
    json_str = route.model_dump_json()
    loaded = RouteDecision.model_validate_json(json_str)
    assert loaded == route


def test_route_decision_properties() -> None:
    """Verify is_workflow and is_supervisor properties on RouteDecision (M1)."""
    wf_static = RouteDecision(
        route_type=RouteType.STATIC_WORKFLOW,
        target_workflow_id="WF-01",
        confidence=0.9,
        domains=[Domain.CALENDAR, Domain.COMMUNICATION],
    )
    assert wf_static.is_workflow is True
    assert wf_static.is_supervisor is False

    wf_known = RouteDecision(
        route_type=RouteType.KNOWN_WORKFLOW,
        workflow_name="meeting_prep",
        confidence=0.9,
        domains=[Domain.GENERAL],
    )
    assert wf_known.is_workflow is True
    assert wf_known.is_supervisor is False

    sup_dag = RouteDecision(
        route_type=RouteType.SUPERVISOR_DAG,
        confidence=0.9,
        domains=[Domain.GENERAL],
    )
    assert sup_dag.is_supervisor is True
    assert sup_dag.is_workflow is False

    sup_legacy = RouteDecision(
        route_type=RouteType.SUPERVISOR,
        confidence=0.9,
        domains=[Domain.GENERAL],
    )
    assert sup_legacy.is_supervisor is True
    assert sup_legacy.is_workflow is False
