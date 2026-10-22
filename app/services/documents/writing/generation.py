"""Outline planning and evidence-grounded section writing.

One optional LLM call plans the outline, then each section is written by the
RAG pipeline restricted to the selected sources, inheriting its citation and
grounding rules; a section without evidence is marked missing, never invented.

The parts live in ``models``, ``planner`` and ``writer``; this module keeps the
names callers import from one place.
"""

from app.services.documents.writing.models import (
    MAX_SECTIONS,
    DocumentOutline,
    OutlineSection,
    WrittenDocument,
    WrittenSection,
)
from app.services.documents.writing.planner import (
    MAX_CANDIDATES,
    MAX_FULL_OUTLINE_SOURCES,
    MAX_OUTLINE_HEADINGS,
    MAX_OUTLINE_LINES,
    MAX_OUTLINE_SNIPPETS,
    MAX_SOURCES,
    OUTLINE_TIMEOUT_SECONDS,
    DocumentPlanner,
    normalize_outline_sections,
)
from app.services.documents.writing.writer import (
    MISSING_SECTION_NOTE,
    SECTION_CONTEXT_TOKENS,
    SectionWriter,
    renumber_document,
)

__all__ = [
    "MAX_CANDIDATES",
    "MAX_FULL_OUTLINE_SOURCES",
    "MAX_OUTLINE_HEADINGS",
    "MAX_OUTLINE_LINES",
    "MAX_OUTLINE_SNIPPETS",
    "MAX_SECTIONS",
    "MAX_SOURCES",
    "MISSING_SECTION_NOTE",
    "OUTLINE_TIMEOUT_SECONDS",
    "SECTION_CONTEXT_TOKENS",
    "DocumentOutline",
    "DocumentPlanner",
    "OutlineSection",
    "SectionWriter",
    "WrittenDocument",
    "WrittenSection",
    "normalize_outline_sections",
    "renumber_document",
]
