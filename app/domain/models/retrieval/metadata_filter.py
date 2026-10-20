"""Contracts for the structured metadata route (count / list / latest / filter).

Questions whose answer is a number or a list of documents ("có bao nhiêu
hợp đồng năm 2026", "liệt kê báo cáo tuần mới nhất") are answered from
``document_profiles`` with SQL, never by an LLM paraphrasing retrieved
chunks. ``MetadataFilterSpec`` is the whitelisted filter language the query
parser may emit; ``StructuredResult`` is what the SQL route returns (also
reused by the spreadsheet engine later).
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KnowledgeIntent(StrEnum):
    """How a knowledge question is answered."""

    METADATA = "metadata"  # answer = count / list from profiles
    METADATA_THEN_CONTENT = "metadata_then_content"  # select docs, then RAG inside them
    CONTENT = "content"  # plain corpus RAG


class MetadataOperation(StrEnum):
    COUNT = "count"
    LIST = "list"
    LATEST = "latest"


class MetadataFilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: MetadataOperation = MetadataOperation.LIST
    categories: tuple[str, ...] = ()
    subtypes: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    issuer: str | None = Field(default=None, max_length=160)
    person: str | None = Field(default=None, max_length=120)
    doc_number: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=20, ge=1, le=50)
    # Human-readable pieces used to explain the answer ("năm 2025", "do X ký").
    descriptions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _dates_ordered(self) -> MetadataFilterSpec:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self

    @property
    def has_selector(self) -> bool:
        """A constraint narrow enough to pick specific documents."""
        return bool(
            self.doc_number
            or self.person
            or self.issuer
            or self.date_from
            or self.date_to
            or self.operation is MetadataOperation.LATEST
        )

    @property
    def has_any_filter(self) -> bool:
        return self.has_selector or bool(self.categories or self.subtypes)


class KnowledgePlan(BaseModel):
    """Parser output: which route to take and with which filters."""

    model_config = ConfigDict(frozen=True)

    intent: KnowledgeIntent
    spec: MetadataFilterSpec = Field(default_factory=MetadataFilterSpec)
    reason: str = ""


class StructuredResult(BaseModel):
    """Deterministic answer material from a SQL route."""

    model_config = ConfigDict(frozen=True)

    kind: str
    total: int
    columns: tuple[str, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    trace: dict[str, Any] = Field(default_factory=dict)
