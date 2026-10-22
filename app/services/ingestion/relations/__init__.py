"""Typed document relations (replaces / based on / signed by / awards …) for SQL lookup."""

from app.services.ingestion.relations.extractor import DocumentRelationExtractor
from app.services.ingestion.relations.types import (
    RELATIONS_EXTRACTOR_VERSION,
    RelationDraft,
    RelationType,
    TargetKind,
)

__all__ = [
    "RELATIONS_EXTRACTOR_VERSION",
    "DocumentRelationExtractor",
    "RelationDraft",
    "RelationType",
    "TargetKind",
]
