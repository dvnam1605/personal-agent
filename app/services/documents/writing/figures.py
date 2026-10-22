"""Offer a section's writer the figures of the source sections it covers.

A section only reads the passages retrieved for it, and a thesis keeps its
diagrams and screenshots in their own chunks, so a report about the system's
architecture would otherwise rarely see "Hình 4.3: Sơ đồ kiến trúc …". Like
its formulas (see formulas.py), each section is told which figures sit under
its planned source headings; the model writes ``[Hình 4.3]`` where a figure
illustrates the paragraph, and the markers resolve against these images.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.images import image_ref
from app.services.documents.writing.formulas import covers
from app.services.retrieval.images import refs_in

MAX_FIGURES_PER_SECTION = 8
HINT_DESCRIPTION_CHARS = 160


@dataclass(frozen=True)
class Figure:
    alt: str  # "Hình 4.3: Sơ đồ kiến trúc — mô tả tự động: …"
    key: str
    section: str  # source heading the figure sits under

    @property
    def label(self) -> str:
        return self.alt.split(":", 1)[0].split(" — ", 1)[0].strip()


def collect_figures(chunks: list[tuple[str, str]]) -> list[Figure]:
    """Figures of stored (heading, text) chunks, in order, each once."""
    seen: set[str] = set()
    figures: list[Figure] = []
    for heading, text in chunks:
        for alt, key, _ in refs_in(text):
            if key not in seen:
                seen.add(key)
                figures.append(Figure(alt=alt, key=key, section=heading))
    return figures


def figure_hint(figures: list[Figure], sources: list[str]) -> tuple[str, str]:
    """(prompt lines, image references) for the figures under ``sources``."""
    chosen = [f for f in figures if covers(sources, f.section)][:MAX_FIGURES_PER_SECTION]
    lines = [
        f"- [{f.label}] {f.alt[len(f.label) :].lstrip(': ')[:HINT_DESCRIPTION_CHARS]}"
        for f in chosen
    ]
    return "\n".join(lines), "\n".join(image_ref(f.alt, f.key) for f in chosen)
