"""Typed action and human-in-the-loop approval models."""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import ActionRiskLevel, ApprovalOutcome


class ProposedAction(BaseModel):
    """A proposed mutation action requiring policy validation or human confirmation (spec P18-02)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the proposed action.",
    )
    action_type: str = Field(
        ...,
        description="Category of action (e.g. 'send_email', 'delete_event', 'share_file').",
    )
    description: str = Field(
        ...,
        description="Human-readable summary of what this action will perform.",
    )
    target: str | None = Field(
        default=None,
        description="Target resource identifier or recipient (e.g. 'alice@example.com', 'event_123').",
    )
    important_arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="High-salience subset of arguments displayed to human approver.",
    )
    tool_name: str | None = Field(
        default=None,
        description="Target tool name to invoke if approved.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters to pass to the tool upon execution.",
    )
    risk_level: ActionRiskLevel = Field(
        default=ActionRiskLevel.LOW_IMPACT_WRITE,
        description="Risk tier of the proposed action.",
    )
    requires_approval: bool = Field(
        default=True,
        description="Whether this action mandates explicit human confirmation.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the proposed action expires if not decided.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the action was proposed.",
    )

    @field_validator("created_at", "expires_at", mode="after")
    @classmethod
    def ensure_utc_aware(cls, v: datetime | None) -> datetime | None:
        """Enforce UTC-aware timestamp."""
        if v is None:
            return None
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("Timestamp must be UTC-aware (e.g. datetime.now(UTC)).")
        return v


class ActionApproval(BaseModel):
    """Recorded human confirmation decision for a proposed action (spec P18-01)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(
        ...,
        description="Reference ID of the ProposedAction.",
    )
    approved: bool = Field(
        ...,
        description="Whether the action was approved or rejected.",
    )
    outcome: ApprovalOutcome = Field(
        default=ApprovalOutcome.ALLOWED_ONCE,
        description="Closed fail-closed outcome classification (allowed-once, rejected, cancelled, unavailable).",
    )
    approver_id: str = Field(
        ...,
        description="Identity of the user or supervisor who made the decision.",
    )
    reason: str | None = Field(
        default=None,
        description="Optional justification or feedback accompanying the decision.",
    )
    decided_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the decision was recorded.",
    )

    @field_validator("decided_at", mode="after")
    @classmethod
    def ensure_utc_aware(cls, v: datetime) -> datetime:
        """Enforce UTC-aware timestamp."""
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("Timestamp must be UTC-aware (e.g. datetime.now(UTC)).")
        return v
