"""Domain enumerations for the personal AI assistant runtime."""

from enum import StrEnum


class Domain(StrEnum):
    """Business domains covered by specialist agents."""

    COMMUNICATION = "communication"
    CALENDAR = "calendar"
    KNOWLEDGE_RESEARCH = "knowledge_research"
    GENERAL = "general"
    SYSTEM = "system"


class Complexity(StrEnum):
    """Task complexity levels for routing decisions."""

    DIRECT = "direct"
    ADAPTIVE = "adaptive"
    MULTI_STEP = "multi_step"
    OPEN_SUPERVISED = "open_supervised"


class RouteType(StrEnum):
    """Execution route classification."""

    DIRECT_SPECIALIST = "direct_specialist"
    STATIC_WORKFLOW = "static_workflow"
    SUPERVISOR_DAG = "supervisor_dag"
    CLARIFICATION = "clarification"
    REJECT = "reject"
    CASUAL_RESPONSE = "casual_response"

    # Legacy enum values (kept strictly for deserialization compatibility with historical DB/wire records)
    KNOWN_WORKFLOW = "known_workflow"
    SUPERVISOR = "supervisor"


def is_workflow_route(route_type: RouteType | str) -> bool:
    """Return True if route_type represents a static or known workflow."""
    val = route_type.value if isinstance(route_type, RouteType) else str(route_type)
    return val in (RouteType.STATIC_WORKFLOW.value, RouteType.KNOWN_WORKFLOW.value)


def is_supervisor_route(route_type: RouteType | str) -> bool:
    """Return True if route_type represents supervisor-led orchestration."""
    val = route_type.value if isinstance(route_type, RouteType) else str(route_type)
    return val in (RouteType.SUPERVISOR_DAG.value, RouteType.SUPERVISOR.value)


class RunStatus(StrEnum):
    """Overall lifecycle status of an execution run."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    APPROVED_UNEXECUTED = "approved_unexecuted"


class TaskStatus(StrEnum):
    """Status of an individual task within a plan."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ActionRiskLevel(StrEnum):
    """Risk tier for tool actions and capabilities."""

    READ_ONLY = "read_only"
    LOW_IMPACT_WRITE = "low_impact_write"
    HIGH_IMPACT_WRITE = "high_impact_write"
    IRREVERSIBLE = "irreversible"


class ActionClass(StrEnum):
    """Canonical policy class attached to every registered tool action."""

    READ = "read"
    SAFE_WRITE = "safe_write"
    SENSITIVE_WRITE = "sensitive_write"
    DESTRUCTIVE = "destructive"
    EXTERNAL_COMMUNICATION = "external_communication"
    PERMISSION_CHANGE = "permission_change"


class ExecutionMode(StrEnum):
    """Default execution strategy declared by an agent."""

    DIRECT = "direct"
    BOUNDED_REACT = "bounded_react"


class StopReason(StrEnum):
    """Why a bounded specialist execution halted (spec P11-03)."""

    SUCCESS = "success"
    NO_PROGRESS = "no_progress"
    MAX_STEPS = "max_steps"
    MAX_TOOL_CALLS = "max_tool_calls"
    BUDGET = "budget"
    TIMEOUT = "timeout"
    POLICY = "policy"


class SpecialistStatus(StrEnum):
    """Outcome status reported through the structured report channel (P11-10)."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    NEEDS_MORE_CONTEXT = "needs_more_context"
    NEEDS_APPROVAL = "needs_approval"


class EvidenceType(StrEnum):
    """Classification of grounded evidence objects."""

    EMAIL = "email"
    CONTACT = "contact"
    CALENDAR_EVENT = "calendar_event"
    DOCUMENT_CHUNK = "document_chunk"
    FILE_METADATA = "file_metadata"
    WEB_SEARCH_RESULT = "web_search_result"
    SYSTEM_FACT = "system_fact"
