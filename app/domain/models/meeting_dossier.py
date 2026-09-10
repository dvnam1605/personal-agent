"""Meeting dossier domain contracts for WF-05 MeetingPrepGraph (P19 §3.2).

Defines structured executive briefing models with attendee profiles,
communication synthesis, document citations, and talking points.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MeetingAttendee(BaseModel):
    """Resolved contact profile for a meeting participant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(..., description="Email address of the participant.")
    name: str | None = Field(default=None, description="Resolved full name if available.")
    role: str | None = Field(default=None, description="Role or position within organization.")
    organization: str | None = Field(
        default=None, description="Company or organization affiliation."
    )


class MeetingDocumentRef(BaseModel):
    """Cited internal or Drive document relevant to the meeting context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    citation_id: str = Field(..., description="Unique evidence citation ID [evidence_id].")
    title: str = Field(..., description="Document title or filename.")
    domain: str = Field(default="knowledge", description="Source domain (rag, drive, etc.).")
    snippet: str | None = Field(default=None, description="Relevant excerpt or summary.")


class MeetingDossier(BaseModel):
    """Canonical structured executive briefing produced by WF-05 MeetingPrepGraph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    meeting_id: str = Field(..., description="Calendar event identifier or target meeting ID.")
    event_summary: str = Field(..., description="Meeting title or calendar summary.")
    scheduled_time: datetime | None = Field(
        default=None,
        description="Scheduled start time with timezone awareness.",
    )
    attendees: list[MeetingAttendee] = Field(
        default_factory=list,
        description="Resolved participant profiles.",
    )
    recent_discussions: list[str] = Field(
        default_factory=list,
        description="Bulleted summary points of recent participant email threads.",
    )
    relevant_documents: list[MeetingDocumentRef] = Field(
        default_factory=list,
        description="Cited internal documents with evidence identifiers.",
    )
    suggested_talking_points: list[str] = Field(
        default_factory=list,
        description="Actionable agenda items and discussion recommendations.",
    )
    unresolved_action_items: list[str] = Field(
        default_factory=list,
        description="Open questions, overdue commitments, or blockers.",
    )
    status: str = Field(
        default="completed",
        description="Workflow completion status ('completed', 'no_meeting_found', 'partial_error').",
    )

    @field_validator("scheduled_time")
    @classmethod
    def ensure_timezone_aware(cls, dt: datetime | None) -> datetime | None:
        """Enforce aware datetime when scheduled_time is provided (P19 §3.2)."""
        if dt is not None and dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
