"""Services package exporting application service contracts.

Imports are lazy (PEP 562) so ``from app.services.google_auth import …`` does not
execute calendar/drive adapters and create an import-order cycle with
``app.integrations`` / ``app.tools`` (N1).
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.approvals import ApprovalRequestService, ApprovalService
    from app.services.audit import (
        AuditOutboxService,
        AuditOutboxWorker,
        AuditService,
        create_sanitized_state_snapshot,
        sanitize_payload,
    )
    from app.services.budget_manager import BudgetManager
    from app.services.calendar import (
        CalendarService,
        GoogleCalendarService,
        busy_intervals_from_events,
        find_deterministic_free_slots,
    )
    from app.services.capability_gate import CapabilityGate
    from app.services.communication import CommunicationService, GoogleCommunicationService
    from app.services.drive import DriveService, GoogleDriveService
    from app.services.google_auth import (
        GoogleApiClient,
        GoogleClientFactory,
        GoogleIntegrationStatus,
        GoogleOAuthClient,
        GoogleOAuthService,
        GoogleScopeValidator,
        GoogleTokenSet,
        InMemoryOAuthStateStore,
    )
    from app.services.policy_engine import PolicyDecision, PolicyEngine
    from app.services.question_plane import QuestionPlaneService
    from app.services.retention import RetentionService, RetentionWorker
    from app.services.run_persistence import RunPersistenceService
    from app.services.skills import SkillExecutor, SkillRegistry, load_production_skills
    from app.services.spill import (
        InMemorySpillStore,
        LocalFileSpillStore,
        SpillPolicy,
        SpillStore,
    )
    from app.services.triage import FastTriage
    from app.services.workflow_registry import (
        StaticWorkflowEntry,
        StaticWorkflowRegistry,
        load_default_workflow_registry,
    )

_EXPORTS: dict[str, tuple[str, str]] = {
    "ApprovalRequestService": ("app.services.approvals", "ApprovalRequestService"),
    "ApprovalService": ("app.services.approvals", "ApprovalService"),
    "AuditOutboxService": ("app.services.audit", "AuditOutboxService"),
    "AuditOutboxWorker": ("app.services.audit", "AuditOutboxWorker"),
    "AuditService": ("app.services.audit", "AuditService"),
    "BudgetManager": ("app.services.budget_manager", "BudgetManager"),
    "CalendarService": ("app.services.calendar", "CalendarService"),
    "CapabilityGate": ("app.services.capability_gate", "CapabilityGate"),
    "CommunicationService": ("app.services.communication", "CommunicationService"),
    "DriveService": ("app.services.drive", "DriveService"),
    "FastTriage": ("app.services.triage", "FastTriage"),
    "GoogleApiClient": ("app.services.google_auth", "GoogleApiClient"),
    "GoogleCalendarService": ("app.services.calendar", "GoogleCalendarService"),
    "GoogleClientFactory": ("app.services.google_auth", "GoogleClientFactory"),
    "GoogleCommunicationService": ("app.services.communication", "GoogleCommunicationService"),
    "GoogleDriveService": ("app.services.drive", "GoogleDriveService"),
    "GoogleIntegrationStatus": ("app.services.google_auth", "GoogleIntegrationStatus"),
    "GoogleOAuthClient": ("app.services.google_auth", "GoogleOAuthClient"),
    "GoogleOAuthService": ("app.services.google_auth", "GoogleOAuthService"),
    "GoogleScopeValidator": ("app.services.google_auth", "GoogleScopeValidator"),
    "GoogleTokenSet": ("app.services.google_auth", "GoogleTokenSet"),
    "InMemoryOAuthStateStore": ("app.services.google_auth", "InMemoryOAuthStateStore"),
    "InMemorySpillStore": ("app.services.spill", "InMemorySpillStore"),
    "LocalFileSpillStore": ("app.services.spill", "LocalFileSpillStore"),
    "PolicyDecision": ("app.services.policy_engine", "PolicyDecision"),
    "PolicyEngine": ("app.services.policy_engine", "PolicyEngine"),
    "QuestionPlaneService": ("app.services.question_plane", "QuestionPlaneService"),
    "RetentionService": ("app.services.retention", "RetentionService"),
    "RetentionWorker": ("app.services.retention", "RetentionWorker"),
    "RunPersistenceService": ("app.services.run_persistence", "RunPersistenceService"),
    "SkillExecutor": ("app.services.skills", "SkillExecutor"),
    "SkillRegistry": ("app.services.skills", "SkillRegistry"),
    "SpillPolicy": ("app.services.spill", "SpillPolicy"),
    "SpillStore": ("app.services.spill", "SpillStore"),
    "StaticWorkflowEntry": ("app.services.workflow_registry", "StaticWorkflowEntry"),
    "StaticWorkflowRegistry": ("app.services.workflow_registry", "StaticWorkflowRegistry"),
    "busy_intervals_from_events": ("app.services.calendar", "busy_intervals_from_events"),
    "create_sanitized_state_snapshot": ("app.services.audit", "create_sanitized_state_snapshot"),
    "find_deterministic_free_slots": ("app.services.calendar", "find_deterministic_free_slots"),
    "load_default_workflow_registry": (
        "app.services.workflow_registry",
        "load_default_workflow_registry",
    ),
    "load_production_skills": ("app.services.skills", "load_production_skills"),
    "sanitize_payload": ("app.services.audit", "sanitize_payload"),
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
