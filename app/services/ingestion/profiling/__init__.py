"""Generic document profiling: category classification + typed metadata."""

from app.services.ingestion.profiling.categories import (
    CATEGORY_SPECS,
    SPEC_BY_CATEGORY,
    DocCategory,
)
from app.services.ingestion.profiling.classifier import Classification, RuleClassifier
from app.services.ingestion.profiling.profiler import (
    EXTRACTOR_VERSION,
    DocumentProfileDraft,
    DocumentProfiler,
)

__all__ = [
    "CATEGORY_SPECS",
    "EXTRACTOR_VERSION",
    "SPEC_BY_CATEGORY",
    "Classification",
    "DocCategory",
    "DocumentProfileDraft",
    "DocumentProfiler",
    "RuleClassifier",
]
