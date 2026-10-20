"""Citation model for verifiable source references (spec P10-19).

Each citation maps to an ``Evidence`` item in the bundle and carries
enough provenance for a UI to render a verifiable reference without
re-running retrieval.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    """One citation reference attached to a synthesised answer (P10-19)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    evidence_id: str
    document_id: str
    title: str = ""
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    source_type: str = ""
    chunk_id: str | None = None
    text: str | None = None
    page_number: int | None = None
    section_title: str | None = None
    score: float | None = None
    source_uri: str | None = None
    # Version the text was taken from: archived versions stay resolvable, so a
    # citation keeps pointing at exactly what the answer was based on.
    document_version_id: str | None = None
    version_number: int | None = None
    # "chunk" for passage evidence, "document" for metadata-route answers.
    citation_type: str = "chunk"
