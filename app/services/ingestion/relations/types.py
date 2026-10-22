"""Relation vocabulary shared by ingestion (extraction) and retrieval (lookup)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

RELATIONS_EXTRACTOR_VERSION = "relations-v1"


class RelationType(StrEnum):
    # document → document (rules)
    REPLACES = "replaces"
    AMENDS = "amends"
    BASED_ON = "based_on"
    PROPOSED_BY = "proposed_by"
    FUNDED_BY = "funded_by"
    # document → person (profile signer)
    SIGNED_BY = "signed_by"
    # document → person / organisation (LLM)
    AWARDS = "awards"
    RECOGNIZES = "recognizes"
    APPOINTS = "appoints"
    TRANSFERS = "transfers"
    DISMISSES = "dismisses"
    TERMINATES = "terminates"
    RECRUITS = "recruits"


DOCUMENT_TARGET_TYPES = frozenset(
    {
        RelationType.REPLACES,
        RelationType.AMENDS,
        RelationType.BASED_ON,
        RelationType.PROPOSED_BY,
        RelationType.FUNDED_BY,
    }
)
PERSONNEL_TYPES = frozenset(
    {
        RelationType.APPOINTS,
        RelationType.TRANSFERS,
        RelationType.DISMISSES,
        RelationType.TERMINATES,
        RelationType.RECRUITS,
    }
)


class TargetKind(StrEnum):
    DOCUMENT = "document"
    PERSON = "person"
    ORGANIZATION = "organization"


class RelationDraft(BaseModel):
    """One relation before it is attached to a stored document version."""

    model_config = ConfigDict(frozen=True)

    relation_type: RelationType
    target_kind: TargetKind
    target_text: str = Field(min_length=1, max_length=400)
    target_norm: str = Field(min_length=1)
    target_number_norm: str | None = None
    target_date: date | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence: str | None = Field(default=None, max_length=600)
    source: str = "rule"  # rule | profile | llm

    def key(self) -> tuple[str, str]:
        return self.relation_type.value, self.target_number_norm or self.target_norm
