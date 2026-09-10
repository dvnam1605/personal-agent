"""P9C chunking engine: ParsedDocument -> deterministic parent/child drafts.

Pure orchestration over the two strategies; no I/O and no embedding calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import ChunkingSettings
from app.core.config import settings as app_settings
from app.domain.models.ingestion.chunks import ChildChunkDraft, ParentChunkDraft
from app.domain.models.ingestion.parsed_document import ParsedDocument
from app.services.ingestion.chunking.children import SentenceChildChunker
from app.services.ingestion.chunking.parents import SectionParentChunker
from app.services.ingestion.chunking.protocols import ChunkContext


@dataclass(frozen=True)
class ChunkDraftSet:
    """All drafts for one document version, parents before their children."""

    parents: tuple[ParentChunkDraft, ...]
    children: tuple[ChildChunkDraft, ...]


def build_chunk_drafts(
    document: ParsedDocument,
    context: ChunkContext,
    *,
    settings: ChunkingSettings | None = None,
) -> ChunkDraftSet:
    """Group into section parents, then split each parent into children."""
    settings = settings or app_settings.chunking
    parents = SectionParentChunker(context, settings).build_parents(document)
    child_chunker = SentenceChildChunker(context, settings)
    children: list[ChildChunkDraft] = []
    for parent in parents:
        children.extend(child_chunker.build_children(parent))
    return ChunkDraftSet(parents=tuple(parents), children=tuple(children))


__all__ = ["ChunkContext", "ChunkDraftSet", "build_chunk_drafts"]
