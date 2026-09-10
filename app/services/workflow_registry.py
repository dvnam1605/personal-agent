"""Static workflow registry and metadata specifications (P15 / §18A.5).

Static workflows represent frequently executed, standardized multi-step
sequences with known dependencies. They bypass the expensive Supervisor LLM
planning loop.

In strict compliance with §18A.5:
- ``app/services/`` maintains ZERO framework imports (no langgraph).
- Compiled StateGraph subgraphs live exclusively under ``app/harness/workflows/``.
- ``StaticWorkflowRegistry`` holds declarative specifications, domain contracts,
  and trigger patterns used by FastTriage for deterministic matching.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.domain.enums import Domain
from app.domain.errors import ConfigurationError, ValidationError
from app.services.skills.matching import match_workflow_trigger

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StaticWorkflowEntry:
    """Registered static workflow specification and optional graph factory."""

    workflow_id: str
    name: str
    description: str
    trigger_patterns: tuple[str, ...]
    graph_factory: Callable[..., Any] | None = None
    domains: tuple[Domain, ...] = (Domain.GENERAL,)
    metadata: dict[str, Any] = field(default_factory=dict)


class StaticWorkflowRegistry:
    """Manages discovery, validation, and trigger matching for static workflows."""

    def __init__(self) -> None:
        self._workflows: dict[str, StaticWorkflowEntry] = {}

    def register(self, entry: StaticWorkflowEntry) -> None:
        """Register one static workflow and reject duplicate IDs."""
        if not isinstance(entry, StaticWorkflowEntry):
            raise ValidationError(
                "StaticWorkflowRegistry.register expects a StaticWorkflowEntry instance.",
                details={"received_type": type(entry).__name__},
            )
        normalized_id = entry.workflow_id.strip()
        if not normalized_id:
            raise ValidationError("workflow_id cannot be blank.")
        if normalized_id in self._workflows:
            raise ConfigurationError(
                f"Workflow '{normalized_id}' is already registered.",
                details={"workflow_id": normalized_id},
            )
        if entry.workflow_id != normalized_id:
            entry = StaticWorkflowEntry(
                workflow_id=normalized_id,
                name=entry.name,
                description=entry.description,
                trigger_patterns=entry.trigger_patterns,
                graph_factory=entry.graph_factory,
                domains=entry.domains,
                metadata=entry.metadata,
            )
        self._workflows[normalized_id] = entry

    def get(self, workflow_id: str) -> StaticWorkflowEntry | None:
        """Lookup a registered workflow by ID."""
        return self._workflows.get(workflow_id.strip())

    def match(self, query: str) -> StaticWorkflowEntry | None:
        """Find the first static workflow matching contiguous trigger patterns in the query."""
        normalized_query = query.strip()
        if not normalized_query:
            return None
        for entry in self._workflows.values():
            if any(
                match_workflow_trigger(trigger, normalized_query)
                for trigger in entry.trigger_patterns
            ):
                return entry
        return None

    def list_all(self) -> list[StaticWorkflowEntry]:
        """List all registered static workflows sorted by ID."""
        return sorted(self._workflows.values(), key=lambda entry: entry.workflow_id)


def load_default_workflow_registry() -> StaticWorkflowRegistry:
    """Build and populate the default static workflow registry with standard definitions."""
    registry = StaticWorkflowRegistry()

    registry.register(
        StaticWorkflowEntry(
            workflow_id="WF-01",
            name="Quick Meeting Follow-up",
            description="Retrieve meeting context, search participant emails, and prepare draft reply.",
            trigger_patterns=(
                "follow-up cuộc họp",
                "quick meeting follow-up",
                "tổng hợp cuộc họp",
                "soạn thư cuộc họp",
                "followup hop",
                "follow-up hop",
                "tong hop cuoc hop",
                "soan thu cuoc hop",
            ),
            domains=(Domain.CALENDAR, Domain.COMMUNICATION),
            metadata={"adr_reference": "ADR 0005"},
        )
    )

    registry.register(
        StaticWorkflowEntry(
            workflow_id="WF-02",
            name="Document Search & Briefing",
            description="Parallel search across RAG and Drive with unified briefing synthesis.",
            trigger_patterns=(
                "tra cứu và tóm tắt",
                "document search and briefing",
                "tìm kiếm và tóm tắt tài liệu",
                "tóm lược tài liệu",
                "briefing tài liệu",
                "tổng hợp tài liệu",
                "tra cuu va tom tat",
                "tim kiem va tom tat tai lieu",
                "tom luoc tai lieu",
                "tong hop tai lieu",
            ),
            domains=(Domain.KNOWLEDGE_RESEARCH,),
            metadata={"adr_reference": "ADR 0005"},
        )
    )

    registry.register(
        StaticWorkflowEntry(
            workflow_id="WF-05",
            name="Meeting Prep Graph",
            description="Deterministically identifies meeting context, runs parallel email & document research, and synthesizes an executive briefing dossier.",
            trigger_patterns=(
                "chuẩn bị họp",
                "chuan bi hop",
                "chuẩn bị cuộc họp",
                "chuan bi cuoc hop",
                "hop ngay mai",
                "họp ngày mai",
                "meeting prep",
                "chuẩn bị tài liệu cuộc họp",
                "chuẩn bị hồ sơ họp",
                "tổng hợp trước cuộc họp",
                "tổng hợp hồ sơ họp",
                "chuẩn bị tài liệu họp",
                "meeting prep graph",
                "meeting-prep graph",
                "hồ sơ cuộc họp",
                "chuan bi ho so hop",
                "chuan bi tai lieu cuoc hop",
                "tong hop ho so hop",
                "wf-05",
                r"/(?:họp|cuộc họp|hop|cuoc hop).*(?:chuẩn bị|chuan bi)|(?:chuẩn bị|chuan bi).*(?:họp|cuộc họp|hop|cuoc hop)/",
            ),
            domains=(Domain.CALENDAR, Domain.COMMUNICATION, Domain.KNOWLEDGE_RESEARCH),
            metadata={"adr_reference": "ADR 0005", "phase": "P19"},
        )
    )

    return registry
