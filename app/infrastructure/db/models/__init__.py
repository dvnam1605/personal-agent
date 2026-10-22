"""SQLAlchemy ORM models for the core database tables with forward-compatible schemas.

Models live in one module per domain; this package imports every one of them so
string-named relationships resolve and ``Base.metadata`` holds every table
(alembic ``env.py`` relies on that), and re-exports them under the old flat path.
"""

from app.infrastructure.db.base import Base, TimestampMixin
from app.infrastructure.db.models.approvals import ApprovalRequest, UserQuestion
from app.infrastructure.db.models.audit import (
    AuditEvent,
    AuditOutbox,
    LLMExecution,
    ToolExecution,
    _forbid_append_only_orm_mutation,
)
from app.infrastructure.db.models.common import (
    _JSONB,
    _sanitize_approval_arguments,
    _sanitize_bounded_text,
    _sanitize_document_metadata,
    _sanitize_json_mapping,
)
from app.infrastructure.db.models.conversations import (
    AssistantRun,
    Conversation,
    Message,
    RunEvidence,
    WorkflowRun,
)
from app.infrastructure.db.models.documents import (
    Document,
    DocumentChunk,
    DocumentProfile,
    DocumentRelation,
    GeneratedDocument,
    IngestionJobRecord,
)
from app.infrastructure.db.models.users import Entity, GoogleIntegration, Memory, Skill, User

__all__ = [
    "_JSONB",
    "ApprovalRequest",
    "AssistantRun",
    "AuditEvent",
    "AuditOutbox",
    "Base",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentProfile",
    "DocumentRelation",
    "Entity",
    "GeneratedDocument",
    "GoogleIntegration",
    "IngestionJobRecord",
    "LLMExecution",
    "Memory",
    "Message",
    "RunEvidence",
    "Skill",
    "TimestampMixin",
    "ToolExecution",
    "User",
    "UserQuestion",
    "WorkflowRun",
    "_forbid_append_only_orm_mutation",
    "_sanitize_approval_arguments",
    "_sanitize_bounded_text",
    "_sanitize_document_metadata",
    "_sanitize_json_mapping",
]
