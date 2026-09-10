"""P9C chunking engine package (pure functions over ParsedDocument)."""

from app.domain.models.ingestion.chunks import ChildChunkDraft, ChunkLevel, ParentChunkDraft
from app.services.ingestion.chunking.children import SentenceChildChunker
from app.services.ingestion.chunking.engine import (
    ChunkContext,
    ChunkDraftSet,
    build_chunk_drafts,
)
from app.services.ingestion.chunking.parents import SectionParentChunker
from app.services.ingestion.chunking.protocols import (
    ChildChunkingStrategy,
    ParentChunkingStrategy,
)
from app.services.ingestion.chunking.tokens import estimate_tokens, normalize_text

__all__ = [
    "ChildChunkDraft",
    "ChildChunkingStrategy",
    "ChunkContext",
    "ChunkDraftSet",
    "ChunkLevel",
    "ParentChunkDraft",
    "ParentChunkingStrategy",
    "SectionParentChunker",
    "SentenceChildChunker",
    "build_chunk_drafts",
    "estimate_tokens",
    "normalize_text",
]
