"""Chunk draft contracts for the P9C chunking engine.

Drafts are pure in-memory values produced from a ParsedDocument; they carry
every field persistence (P9D) will need so parent/child relations stay
queryable without reparsing the source. No embedding vectors here.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChunkLevel(StrEnum):
    """Hierarchy level of a chunk draft (spec P9C-2)."""

    PARENT = "PARENT"
    CHILD = "CHILD"
    TABLE_CHILD = "TABLE_CHILD"


def _validate_sha256(value: str) -> str:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("content_hash must be a 64-char lowercase sha256 hex digest")
    return value


class _ChunkDraftBase(BaseModel):
    """Fields shared by every draft."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=8, max_length=64)
    document_id: str = Field(min_length=1)
    document_version_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    heading_path: tuple[str, ...] = ()
    raw_text: str = Field(min_length=1)
    token_count: int = Field(gt=0)
    content_hash: str
    source_block_ids: tuple[str, ...] = ()
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)

    @field_validator("content_hash")
    @classmethod
    def hash_is_sha256(cls, value: str) -> str:
        return _validate_sha256(value)


class ParentChunkDraft(_ChunkDraftBase):
    """Semantic context unit for generation-time expansion.

    ``segment_anchors`` mirrors the ``\\n\\n``-separated raw segments by index
    with ``(block_id, page_start, page_end)`` per segment; the synthetic
    heading-context segment (review L5) carries a ``None`` block id. This is
    in-memory provenance for the child strategy — persistence maps the
    contract fields only.
    """

    level: ChunkLevel = ChunkLevel.PARENT
    ordinal: int = Field(ge=0)
    parent_chunker_version: str = Field(min_length=1)
    segment_anchors: tuple[tuple[str | None, int | None, int | None], ...] = ()


class ChildChunkDraft(_ChunkDraftBase):
    """Precise retrieval unit; embedded and indexed in V1 (spec 9.3)."""

    level: ChunkLevel = ChunkLevel.CHILD
    parent_id: str = Field(min_length=8, max_length=64)
    chunk_index: int = Field(ge=0)
    embedding_text: str = Field(min_length=1)
    child_chunker_version: str = Field(min_length=1)
    filename: str = Field(min_length=1)  # P9C-7 minimum, enforced (review L4)
    mime_type: str | None = None
    source_type: str | None = None
    embedding_model: str | None = None
