"""Cheap, deterministic signals for classifying and profiling a document.

Signals are built either from the parsed document tree (ingestion) or from
plain text reassembled from stored parent chunks (metadata-only backfill),
so the profiler never needs the original bytes. Line breaks are preserved:
header/tail heuristics depend on them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.models.ingestion.parsed_document import (
    HeadingNode,
    ListNode,
    ParsedDocument,
    TableNode,
)
from app.services.skills.matching import unaccent_vietnamese

HEADER_LINES = 60
TAIL_LINES = 40
_HTML_TAG = re.compile(r"<[^>]+>")
_LEADER_DOTS = re.compile(r"\.{4,}")


@dataclass(frozen=True)
class DocumentSignals:
    filename: str
    header_lines: tuple[str, ...]
    tail_lines: tuple[str, ...]
    headings: tuple[str, ...] = ()
    table_count: int = 0
    node_count: int = 0
    page_count: int | None = None
    text_length: int = 0
    # Accent-folded lowercase views used by every regex rule.
    header_folded: str = ""
    tail_folded: str = ""
    filename_folded: str = ""
    vietnamese_ratio: float = 0.0
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def header_text(self) -> str:
        return "\n".join(self.header_lines)

    @property
    def tail_text(self) -> str:
        return "\n".join(self.tail_lines)


def _clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = _HTML_TAG.sub(" ", raw)
        line = _LEADER_DOTS.sub(" ", line)
        line = re.sub(r"\s+", " ", line).strip(" \t|*_")
        if line:
            lines.append(line)
    return lines


_VI_CHARS = re.compile(r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]")


def _vietnamese_ratio(text: str) -> float:
    letters = [c for c in text.lower() if c.isalpha()]
    if not letters:
        return 0.0
    return len(_VI_CHARS.findall("".join(letters))) / len(letters)


def build_signals_from_text(
    text: str,
    filename: str,
    *,
    headings: tuple[str, ...] = (),
    table_count: int = 0,
    node_count: int = 0,
    page_count: int | None = None,
) -> DocumentSignals:
    lines = _clean_lines(text)
    header = tuple(lines[:HEADER_LINES])
    tail = tuple(lines[-TAIL_LINES:]) if len(lines) > HEADER_LINES else ()
    sample = "\n".join(lines[: HEADER_LINES * 3])
    return DocumentSignals(
        filename=filename,
        header_lines=header,
        tail_lines=tail,
        headings=headings,
        table_count=table_count,
        node_count=node_count,
        page_count=page_count,
        text_length=len(text),
        header_folded=unaccent_vietnamese_lines(header),
        tail_folded=unaccent_vietnamese_lines(tail),
        filename_folded=unaccent_vietnamese(filename),
        vietnamese_ratio=_vietnamese_ratio(sample),
    )


def unaccent_vietnamese_lines(lines: tuple[str, ...]) -> str:
    """Fold each line separately so line anchors (``^``) still work."""
    return "\n".join(unaccent_vietnamese(line) for line in lines)


def document_text(document: ParsedDocument) -> str:
    """Plain text of the tree in reading order, one block per line group."""
    parts: list[str] = []
    for node in document.nodes:
        if isinstance(node, TableNode):
            parts.append(node.markdown)
        elif isinstance(node, ListNode) and node.items:
            parts.extend(node.items)
        elif node.text:
            parts.append(node.text)
    return "\n".join(parts)


def build_signals(document: ParsedDocument) -> DocumentSignals:
    headings = tuple(n.text.strip() for n in document.nodes if isinstance(n, HeadingNode))
    tables = sum(1 for n in document.nodes if isinstance(n, TableNode))
    return build_signals_from_text(
        document_text(document),
        document.metadata.filename,
        headings=headings[:80],
        table_count=tables,
        node_count=len(document.nodes),
        page_count=document.metadata.page_count,
    )
