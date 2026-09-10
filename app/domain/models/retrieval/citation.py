"""Citation model for verifiable source references (spec P10-19).

Each citation maps to an ``Evidence`` item in the bundle and carries
enough provenance for a UI to render a verifiable reference without
re-running retrieval.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    """One citation reference attached to a synthesised answer (P10-19)."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    document_id: str
    title: str = ""
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    source_type: str = ""
