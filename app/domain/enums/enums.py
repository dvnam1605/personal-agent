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
    KNOWN_WORKFLOW = "known_workflow"
    SUPERVISOR = "supervisor"
    CASUAL_RESPONSE = "casual_response"


class RunStatus(StrEnum):
    """Overall lifecycle status of an execution run."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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


class EvidenceType(StrEnum):
    """Classification of grounded evidence objects."""

    EMAIL = "email"
    CONTACT = "contact"
    CALENDAR_EVENT = "calendar_event"
    DOCUMENT_CHUNK = "document_chunk"
    FILE_METADATA = "file_metadata"
    WEB_SEARCH_RESULT = "web_search_result"
    SYSTEM_FACT = "system_fact"
