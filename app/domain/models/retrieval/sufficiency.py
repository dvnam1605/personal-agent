"""Sufficiency domain contracts (spec P10-16).

Three-state verdict derived from retrieval coverage and score distribution;
deterministic rules only — no LLM judge in V1.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SufficiencyStatus(StrEnum):
    """Retrieval evidence sufficiency verdicts (spec P10-16)."""

    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class SufficiencyVerdict(BaseModel):
    """Result of a deterministic sufficiency check on an EvidenceBundle."""

    model_config = ConfigDict(frozen=True)

    status: SufficiencyStatus
    missing_document_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    retry_hint: str | None = None
