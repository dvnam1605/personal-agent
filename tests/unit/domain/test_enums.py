"""Unit tests for domain enums."""

import json

from app.domain.enums import (
    ActionRiskLevel,
    Complexity,
    Domain,
    EvidenceType,
    RouteType,
    RunStatus,
    TaskStatus,
)


def test_domain_enum_values() -> None:
    """Verify Domain enum members and string representations."""
    assert Domain.COMMUNICATION == "communication"
    assert Domain.CALENDAR == "calendar"
    assert Domain.KNOWLEDGE_RESEARCH == "knowledge_research"
    assert Domain.GENERAL == "general"
    assert Domain.SYSTEM == "system"


def test_complexity_enum_values() -> None:
    """Verify Complexity enum values."""
    assert Complexity.DIRECT == "direct"
    assert Complexity.ADAPTIVE == "adaptive"
    assert Complexity.MULTI_STEP == "multi_step"
    assert Complexity.OPEN_SUPERVISED == "open_supervised"


def test_route_type_enum_values() -> None:
    """Verify RouteType enum values, legacy strings, and wire round-trip (M3)."""
    assert RouteType.DIRECT_SPECIALIST == "direct_specialist"
    assert RouteType.STATIC_WORKFLOW == "static_workflow"
    assert RouteType.SUPERVISOR_DAG == "supervisor_dag"
    assert RouteType.CLARIFICATION == "clarification"
    assert RouteType.REJECT == "reject"
    assert RouteType.CASUAL_RESPONSE == "casual_response"

    # Distinct legacy string values for DB/wire round-trip compatibility
    assert RouteType.KNOWN_WORKFLOW == "known_workflow"
    assert RouteType("known_workflow") == RouteType.KNOWN_WORKFLOW
    assert RouteType.SUPERVISOR == "supervisor"
    assert RouteType("supervisor") == RouteType.SUPERVISOR

    # Predicate helpers (M2: standalone functions are the single source of truth)
    from app.domain.enums import is_supervisor_route, is_workflow_route

    assert is_workflow_route(RouteType.STATIC_WORKFLOW) is True
    assert is_workflow_route(RouteType.KNOWN_WORKFLOW) is True
    assert is_workflow_route(RouteType.DIRECT_SPECIALIST) is False
    assert is_workflow_route("known_workflow") is True
    assert is_workflow_route("static_workflow") is True
    assert is_workflow_route("direct_specialist") is False

    assert is_supervisor_route(RouteType.SUPERVISOR_DAG) is True
    assert is_supervisor_route(RouteType.SUPERVISOR) is True
    assert is_supervisor_route(RouteType.DIRECT_SPECIALIST) is False
    assert is_supervisor_route("supervisor_dag") is True
    assert is_supervisor_route("supervisor") is True
    assert is_supervisor_route("direct_specialist") is False


def test_run_status_enum_values() -> None:
    """Verify RunStatus enum values."""
    assert RunStatus.PENDING == "pending"
    assert RunStatus.RUNNING == "running"
    assert RunStatus.WAITING_INPUT == "waiting_input"
    assert RunStatus.WAITING_APPROVAL == "waiting_approval"
    assert RunStatus.COMPLETED == "completed"
    assert RunStatus.FAILED == "failed"
    assert RunStatus.CANCELLED == "cancelled"
    assert RunStatus.APPROVED_UNEXECUTED == "approved_unexecuted"


def test_task_status_enum_values() -> None:
    """Verify TaskStatus enum values."""
    assert TaskStatus.PENDING == "pending"
    assert TaskStatus.RUNNING == "running"
    assert TaskStatus.COMPLETED == "completed"
    assert TaskStatus.FAILED == "failed"
    assert TaskStatus.SKIPPED == "skipped"
    assert TaskStatus.BLOCKED == "blocked"


def test_action_risk_level_enum_values() -> None:
    """Verify ActionRiskLevel values."""
    assert ActionRiskLevel.READ_ONLY == "read_only"
    assert ActionRiskLevel.LOW_IMPACT_WRITE == "low_impact_write"
    assert ActionRiskLevel.HIGH_IMPACT_WRITE == "high_impact_write"
    assert ActionRiskLevel.IRREVERSIBLE == "irreversible"


def test_evidence_type_enum_values() -> None:
    """Verify EvidenceType values."""
    assert EvidenceType.EMAIL == "email"
    assert EvidenceType.CONTACT == "contact"
    assert EvidenceType.CALENDAR_EVENT == "calendar_event"
    assert EvidenceType.DOCUMENT_CHUNK == "document_chunk"
    assert EvidenceType.FILE_METADATA == "file_metadata"
    assert EvidenceType.WEB_SEARCH_RESULT == "web_search_result"
    assert EvidenceType.SYSTEM_FACT == "system_fact"


def test_enum_json_serialization() -> None:
    """Verify enums cleanly serialize to JSON strings."""
    data = {
        "domain": Domain.COMMUNICATION,
        "status": RunStatus.RUNNING,
        "risk": ActionRiskLevel.HIGH_IMPACT_WRITE,
    }
    dumped = json.dumps(data)
    assert dumped == '{"domain": "communication", "status": "running", "risk": "high_impact_write"}'
