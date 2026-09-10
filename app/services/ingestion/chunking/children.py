"""SentenceChildChunker: CHILD / TABLE_CHILD drafts within one parent.

Boundary priority inside a single parent (spec 9.2): paragraph/list segment
boundaries first, then sentences, then a hard token cut. Children inherit the
parent's identity and heading path; provenance stays sharp (review M2): each
child anchors to the specific parent segment(s) it was built from via
``segment_anchors``, not to the whole-parent union. Overlap: none in V1
(rule 9.4 permits zero overlap when boundaries are already semantic).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.core.config import ChunkingSettings
from app.core.config import settings as app_settings
from app.domain.models.ingestion.chunks import ChildChunkDraft, ChunkLevel, ParentChunkDraft
from app.services.ingestion.chunking.identity import (
    CHILD_CHUNKER_VERSION,
    child_chunk_id,
    content_hash,
)
from app.services.ingestion.chunking.tokens import (
    estimate_tokens,
    normalize_text,
    split_sentences,
    truncate_at_token_boundary,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.ingestion.chunking.parents import SegmentAnchor
    from app.services.ingestion.chunking.protocols import ChunkContext

_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")


def _embedding_text(heading_path: tuple[str, ...], text: str) -> str:
    """Lightweight context enrichment; adds no claims absent from the source."""
    if not heading_path:
        return text
    return f"Section: {' > '.join(heading_path)}\n\n{text}"


class _Unit:
    """One child candidate with its exact source provenance."""

    __slots__ = ("level", "text", "anchors")

    def __init__(
        self,
        level: ChunkLevel,
        text: str,
        anchors: tuple[SegmentAnchor, ...],
    ) -> None:
        self.level = level
        self.text = text
        self.anchors = anchors

    def extended_with(self, other: _Unit) -> _Unit:
        return _Unit(
            self.level,
            f"{self.text}\n\n{other.text}",
            self.anchors + other.anchors,
        )


def _is_table_segment(segment: str) -> bool:
    lines = [line for line in segment.splitlines() if line.strip().startswith("|")]
    return len(lines) >= 2 and any(_TABLE_SEPARATOR_RE.match(line) for line in lines)


def _table_units(segment: str, anchor: SegmentAnchor, settings: ChunkingSettings) -> list[_Unit]:
    """Small table -> one unit; large table -> header-repeating row groups."""
    lines = [line for line in segment.splitlines() if line.strip()]
    separator_index = next(
        (i for i, line in enumerate(lines) if _TABLE_SEPARATOR_RE.match(line)),
        None,
    )
    # L1: explicit guards — a separator at index 0 means "no header row";
    # truthiness alone would misclassify it as "no table".
    if separator_index is None or separator_index == 0:
        return [_Unit(ChunkLevel.TABLE_CHILD, segment, (anchor,))]

    header = lines[separator_index - 1]
    separator = lines[separator_index]
    body_rows = lines[separator_index + 1 :]
    if not body_rows:
        return [_Unit(ChunkLevel.TABLE_CHILD, segment, (anchor,))]

    budget = settings.child_hard_max_tokens
    header_tokens = estimate_tokens(header) + estimate_tokens(separator)
    units: list[_Unit] = []
    group: list[str] = []
    group_tokens = 0
    for row in body_rows:
        row_tokens = estimate_tokens(row)
        # L2: a single oversized row stays atomic (never character-split a
        # table row); it becomes its own budget-exceeding TABLE_CHILD.
        if row_tokens + header_tokens > budget:
            if group:
                units.append(
                    _Unit(
                        ChunkLevel.TABLE_CHILD,
                        "\n".join([header, separator, *group]),
                        (anchor,),
                    )
                )
                group, group_tokens = [], 0
            units.append(
                _Unit(ChunkLevel.TABLE_CHILD, "\n".join([header, separator, row]), (anchor,))
            )
            continue
        if group and group_tokens + row_tokens > budget:
            units.append(
                _Unit(
                    ChunkLevel.TABLE_CHILD,
                    "\n".join([header, separator, *group]),
                    (anchor,),
                )
            )
            group, group_tokens = [], 0
        group.append(row)
        group_tokens += row_tokens
    if group:
        units.append(
            _Unit(ChunkLevel.TABLE_CHILD, "\n".join([header, separator, *group]), (anchor,))
        )
    return units


def _text_units(segment: str, anchor: SegmentAnchor, settings: ChunkingSettings) -> list[_Unit]:
    """One unit when it fits; else sentence packing to target; token-cut last."""
    if estimate_tokens(segment) <= settings.child_hard_max_tokens:
        return [_Unit(ChunkLevel.CHILD, segment, (anchor,))]

    pieces: list[_Unit] = []
    buffer: list[str] = []
    buffer_tokens = 0

    def flush_buffer() -> None:
        nonlocal buffer, buffer_tokens
        if buffer:
            pieces.append(_Unit(ChunkLevel.CHILD, normalize_text("\n".join(buffer)), (anchor,)))
            buffer, buffer_tokens = [], 0

    for sentence in split_sentences(segment):
        sentence_tokens = estimate_tokens(sentence)
        if sentence_tokens > settings.child_hard_max_tokens:
            flush_buffer()
            remaining = sentence
            while remaining and estimate_tokens(remaining) > settings.child_hard_max_tokens:
                target = settings.child_target_tokens
                cut, consumed = truncate_at_token_boundary(remaining, target)
                if not cut or consumed >= len(remaining):
                    cut, consumed = truncate_at_token_boundary(
                        remaining, settings.child_hard_max_tokens
                    )
                pieces.append(_Unit(ChunkLevel.CHILD, cut.strip(), (anchor,)))
                remaining = remaining[consumed:].strip()
            if remaining:
                buffer, buffer_tokens = [remaining], estimate_tokens(remaining)
            continue
        if buffer_tokens + sentence_tokens > settings.child_target_tokens and buffer:
            flush_buffer()
        buffer.append(sentence)
        buffer_tokens += sentence_tokens
    flush_buffer()
    return [unit for unit in pieces if normalize_text(unit.text)]


def _merge_small_units(units: list[_Unit], settings: ChunkingSettings) -> list[_Unit]:
    """Merge very small adjacent plain-text units within the same parent."""
    merged: list[_Unit] = []
    for unit in units:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.level is ChunkLevel.CHILD
            and unit.level is ChunkLevel.CHILD
            and estimate_tokens(previous.text) < settings.merge_small_nodes_below_tokens
            and estimate_tokens(previous.text) + estimate_tokens(unit.text)
            <= settings.child_hard_max_tokens
        ):
            merged[-1] = previous.extended_with(unit)
            continue
        merged.append(unit)
    return merged


def _pages_of(anchors: tuple[SegmentAnchor, ...]) -> tuple[int | None, int | None]:
    starts = [a[1] for a in anchors if a[1] is not None]
    ends = [a[2] for a in anchors if a[2] is not None]
    return (
        min(starts) if starts else None,
        max(ends) if ends else None,
    )


class SentenceChildChunker:
    """Builds CHILD/TABLE_CHILD drafts from a single PARENT draft."""

    def __init__(
        self,
        context: ChunkContext,
        settings: ChunkingSettings | None = None,
    ) -> None:
        self._context = context
        self._settings = settings if settings is not None else app_settings.chunking

    def build_children(self, parent: ParentChunkDraft) -> list[ChildChunkDraft]:
        segments = [
            normalized
            for segment in parent.raw_text.split("\n\n")
            if (normalized := normalize_text(segment))
        ]
        anchors = parent.segment_anchors or ((None, parent.page_start, parent.page_end),) * len(
            segments
        )

        heading_segment = "\n".join(parent.heading_path)
        units: list[_Unit] = []
        for index, segment in enumerate(segments):
            anchor = anchors[index] if index < len(anchors) else (None, None, None)
            # L5 companion: the synthetic heading-context segment exists only
            # for parent-level expansion; retrieval precision comes from the
            # Section: prefix, so it never becomes its own child.
            if parent.heading_path and segment == heading_segment:
                continue
            if _is_table_segment(segment):
                units.extend(_table_units(segment, anchor, self._settings))
            else:
                units.extend(_text_units(segment, anchor, self._settings))
        units = _merge_small_units(units, self._settings)

        block_range = (
            parent.source_block_ids[0] if parent.source_block_ids else None,
            parent.source_block_ids[-1] if parent.source_block_ids else None,
        )
        drafts: list[ChildChunkDraft] = []
        for index, unit in enumerate(units):
            digest = content_hash(unit.text)
            chunk_id = child_chunk_id(
                parent_id=parent.id,
                block_range=block_range,
                ordinal=index,
                content_hash=digest,
            )
            page_start, page_end = _pages_of(unit.anchors)
            drafts.append(
                ChildChunkDraft(
                    id=chunk_id,
                    level=unit.level,
                    document_id=parent.document_id,
                    document_version_id=parent.document_version_id,
                    source_id=parent.source_id,
                    title=parent.title,
                    filename=self._context.filename,
                    mime_type=self._context.mime_type,
                    source_type=self._context.source_type,
                    embedding_model=self._context.embedding_model,
                    parent_id=parent.id,
                    heading_path=parent.heading_path,
                    chunk_index=index,
                    raw_text=unit.text,
                    embedding_text=_embedding_text(parent.heading_path, unit.text),
                    token_count=estimate_tokens(unit.text),
                    content_hash=digest,
                    source_block_ids=tuple(a[0] for a in unit.anchors if a[0] is not None),
                    page_start=page_start,
                    page_end=page_end,
                    child_chunker_version=CHILD_CHUNKER_VERSION,
                    administrative_metadata=dict(parent.administrative_metadata),
                )
            )
        return drafts
