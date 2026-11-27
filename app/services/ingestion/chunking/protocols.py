"""Strategy Protocols and the chunking context value object.

The Protocols match the specification signatures exactly; strategies receive
identity inputs and budgets through their constructors so the Protocol
methods stay pure transforms over ParsedDocument / drafts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.domain.models.ingestion.chunks import ChildChunkDraft, ParentChunkDraft
from app.domain.models.ingestion.parsed_document import ParsedDocument


@dataclass(frozen=True)
class ChunkContext:
    """Identity inputs for one document version's chunks.

       ``filename`` is required — it is part of the child metadata minimum
    and enforced at draft-construction time.
    """

    document_id: str
    document_version_id: str
    source_id: str
    title: str
    filename: str
    mime_type: str | None = None
    source_type: str | None = None
    embedding_model: str | None = None


@runtime_checkable
class ParentChunkingStrategy(Protocol):
    """Builds parent chunks from a parsed document."""

    def build_parents(
        self,
        document: ParsedDocument,
    ) -> list[ParentChunkDraft]: ...


@runtime_checkable
class ChildChunkingStrategy(Protocol):
    """Builds child chunks from one parent chunk."""

    def build_children(
        self,
        parent: ParentChunkDraft,
    ) -> list[ChildChunkDraft]: ...
