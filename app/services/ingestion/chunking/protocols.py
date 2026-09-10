"""Strategy Protocols and the chunking context value object (spec P9C-1).

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
    """Identity inputs for one document version's chunks (spec P9C-2/3/4).

    ``filename`` is required — it is part of the child metadata minimum
    (P9C-7) and enforced at draft-construction time (review L4).
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
    def build_parents(
        self,
        document: ParsedDocument,
    ) -> list[ParentChunkDraft]: ...


@runtime_checkable
class ChildChunkingStrategy(Protocol):
    def build_children(
        self,
        parent: ParentChunkDraft,
    ) -> list[ChildChunkDraft]: ...
