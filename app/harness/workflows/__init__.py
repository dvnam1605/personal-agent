"""Harness compiled static workflows and StateGraph builders (P15 / §18A.5)."""

from __future__ import annotations

from app.harness.workflows.document_briefing import build_document_briefing_graph
from app.harness.workflows.meeting_followup import build_meeting_followup_graph

__all__ = [
    "build_document_briefing_graph",
    "build_meeting_followup_graph",
]
