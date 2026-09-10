"""Services package exporting application service contracts.

Imports are lazy (PEP 562) so ``from app.services.google.auth import …`` does not
execute calendar/drive adapters and create an import-order cycle with
``app.integrations`` / ``app.tools`` (N1).
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.approvals import ApprovalRequestService, ApprovalService
    from app.services.approvals.policy_engine import PolicyDecision, PolicyEngine
    from app.services.approvals.question_plane import QuestionPlaneService
    from app.services.google.auth import (
        GoogleApiClient,
        GoogleClientFactory,
        GoogleIntegrationStatus,
        GoogleOAuthClient,
        GoogleOAuthService,
        GoogleScopeValidator,
        GoogleTokenSet,
        InMemoryOAuthStateStore,
    )
    from app.services.google.calendar import (
        CalendarService,
        GoogleCalendarService,
        busy_intervals_from_events,
        find_deterministic_free_slots,
    )
    from app.services.google.communication import CommunicationService, GoogleCommunicationService
    from app.services.google.drive import DriveService, GoogleDriveService
    from app.services.platform.audit import (
        AuditOutboxService,
        AuditOutboxWorker,
        AuditService,
        create_sanitized_state_snapshot,
        sanitize_payload,
    )
    from app.services.platform.budget_manager import BudgetManager
    from app.services.platform.retention import RetentionService, RetentionWorker
    from app.services.platform.run_persistence import RunPersistenceService
    from app.services.platform.spill import (
        InMemorySpillStore,
        LocalFileSpillStore,
        SpillPolicy,
        SpillStore,
    )
    from app.services.routing.capability_gate import CapabilityGate
    from app.services.routing.triage import FastTriage
    from app.services.routing.workflow_registry import (
        StaticWorkflowEntry,
        StaticWorkflowRegistry,
        load_default_workflow_registry,
    )
    from app.services.skills import SkillExecutor, SkillRegistry, load_production_skills

_EXPORTS: dict[str, tuple[str, str]] = {
    "ApprovalRequestService": ("app.services.approvals", "ApprovalRequestService"),
    "ApprovalService": ("app.services.approvals", "ApprovalService"),
    "AuditOutboxService": ("app.services.platform.audit", "AuditOutboxService"),
    "AuditOutboxWorker": ("app.services.platform.audit", "AuditOutboxWorker"),
    "AuditService": ("app.services.platform.audit", "AuditService"),
    "BudgetManager": ("app.services.platform.budget_manager", "BudgetManager"),
    "CalendarService": ("app.services.google.calendar", "CalendarService"),
    "CapabilityGate": ("app.services.routing.capability_gate", "CapabilityGate"),
    "CommunicationService": ("app.services.google.communication", "CommunicationService"),
    "DriveService": ("app.services.google.drive", "DriveService"),
    "FastTriage": ("app.services.routing.triage", "FastTriage"),
    "GoogleApiClient": ("app.services.google.auth", "GoogleApiClient"),
    "GoogleCalendarService": ("app.services.google.calendar", "GoogleCalendarService"),
    "GoogleClientFactory": ("app.services.google.auth", "GoogleClientFactory"),
    "GoogleCommunicationService": (
        "app.services.google.communication",
        "GoogleCommunicationService",
    ),
    "GoogleDriveService": ("app.services.google.drive", "GoogleDriveService"),
    "GoogleIntegrationStatus": ("app.services.google.auth", "GoogleIntegrationStatus"),
    "GoogleOAuthClient": ("app.services.google.auth", "GoogleOAuthClient"),
    "GoogleOAuthService": ("app.services.google.auth", "GoogleOAuthService"),
    "GoogleScopeValidator": ("app.services.google.auth", "GoogleScopeValidator"),
    "GoogleTokenSet": ("app.services.google.auth", "GoogleTokenSet"),
    "InMemoryOAuthStateStore": ("app.services.google.auth", "InMemoryOAuthStateStore"),
    "InMemorySpillStore": ("app.services.platform.spill", "InMemorySpillStore"),
    "LocalFileSpillStore": ("app.services.platform.spill", "LocalFileSpillStore"),
    "PolicyDecision": ("app.services.approvals.policy_engine", "PolicyDecision"),
    "PolicyEngine": ("app.services.approvals.policy_engine", "PolicyEngine"),
    "QuestionPlaneService": ("app.services.approvals.question_plane", "QuestionPlaneService"),
    "RetentionService": ("app.services.platform.retention", "RetentionService"),
    "RetentionWorker": ("app.services.platform.retention", "RetentionWorker"),
    "RunPersistenceService": ("app.services.platform.run_persistence", "RunPersistenceService"),
    "SkillExecutor": ("app.services.skills", "SkillExecutor"),
    "SkillRegistry": ("app.services.skills", "SkillRegistry"),
    "SpillPolicy": ("app.services.platform.spill", "SpillPolicy"),
    "SpillStore": ("app.services.platform.spill", "SpillStore"),
    "StaticWorkflowEntry": ("app.services.routing.workflow_registry", "StaticWorkflowEntry"),
    "StaticWorkflowRegistry": ("app.services.routing.workflow_registry", "StaticWorkflowRegistry"),
    "busy_intervals_from_events": ("app.services.google.calendar", "busy_intervals_from_events"),
    "create_sanitized_state_snapshot": (
        "app.services.platform.audit",
        "create_sanitized_state_snapshot",
    ),
    "find_deterministic_free_slots": (
        "app.services.google.calendar",
        "find_deterministic_free_slots",
    ),
    "load_default_workflow_registry": (
        "app.services.routing.workflow_registry",
        "load_default_workflow_registry",
    ),
    "load_production_skills": ("app.services.skills", "load_production_skills"),
    "sanitize_payload": ("app.services.platform.audit", "sanitize_payload"),
}


def __getattr__(name: str) -> Any:
    spec = _EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(spec[0])
    value = getattr(module, spec[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(_EXPORTS)


__all__ = [
    "ApprovalRequestService",
    "ApprovalService",
    "AuditOutboxService",
    "AuditOutboxWorker",
    "AuditService",
    "BudgetManager",
    "CalendarService",
    "CapabilityGate",
    "CommunicationService",
    "DriveService",
    "FastTriage",
    "GoogleApiClient",
    "GoogleCalendarService",
    "GoogleClientFactory",
    "GoogleCommunicationService",
    "GoogleDriveService",
    "GoogleIntegrationStatus",
    "GoogleOAuthClient",
    "GoogleOAuthService",
    "GoogleScopeValidator",
    "GoogleTokenSet",
    "InMemoryOAuthStateStore",
    "InMemorySpillStore",
    "LocalFileSpillStore",
    "PolicyDecision",
    "PolicyEngine",
    "QuestionPlaneService",
    "RetentionService",
    "RetentionWorker",
    "RunPersistenceService",
    "SkillExecutor",
    "SkillRegistry",
    "SpillPolicy",
    "SpillStore",
    "StaticWorkflowEntry",
    "StaticWorkflowRegistry",
    "busy_intervals_from_events",
    "create_sanitized_state_snapshot",
    "find_deterministic_free_slots",
    "load_default_workflow_registry",
    "load_production_skills",
    "sanitize_payload",
]
