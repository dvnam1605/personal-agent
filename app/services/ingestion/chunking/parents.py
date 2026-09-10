"""SectionParentChunker: heading-scoped PARENT drafts (spec P9C-1 9.1).

Parents correspond to sections / logical section groups and never mix
unrelated top-level headings. Oversized sections split greedily at node
(paragraph-group) boundaries against exact joined sizes; a single oversized
node falls back sentence-packing -> hard token cut. Smaller-than-target
natural sections stay intact.

Provenance fidelity (review M2): every draft records ``segment_anchors`` —
one ``(block_id, page_start, page_end)`` tuple per ``\\n\\n``-separated raw
segment, aligned by index — so children can anchor to their specific source
node instead of the whole-parent union.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import ChunkingSettings
from app.core.config import settings as app_settings
from app.domain.models.chunks import ChunkLevel, ParentChunkDraft
from app.domain.models.parsed_document import (
    HeadingNode,
    ParsedDocument,
    ParsedNode,
    SourceAnchor,
)
from app.services.ingestion.chunking.identity import (
    PARENT_CHUNKER_VERSION,
    content_hash,
    parent_chunk_id,
)
from app.services.ingestion.chunking.tokens import (
    TextMetrics,
    estimate_from_metrics,
    estimate_tokens,
    normalize_text,
    split_sentences,
    sum_metrics,
    text_metrics,
    truncate_at_token_boundary,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.ingestion.chunking.protocols import ChunkContext

SegmentAnchor = tuple[str | None, int | None, int | None]


def _page_union(nodes: list[ParsedNode]) -> tuple[int | None, int | None]:
    starts: list[int] = []
    ends: list[int] = []
    for node in nodes:
        anchor = node.anchor
        if isinstance(anchor, SourceAnchor) and anchor.page_start is not None:
            starts.append(anchor.page_start)
            ends.append(anchor.page_end or anchor.page_start)
    if not starts:
        return None, None
    return min(starts), max(ends)


def _anchor_of(node: ParsedNode) -> SegmentAnchor:
    anchor = node.anchor
    if isinstance(anchor, SourceAnchor):
        return (node.node_id, anchor.page_start, anchor.page_end)
    return (node.node_id, None, None)


class SectionParentChunker:
    """Builds PARENT drafts from the normalized document tree."""

    def __init__(
        self,
        context: ChunkContext,
        settings: ChunkingSettings | None = None,
    ) -> None:
        self._context = context
        self._settings = settings if settings is not None else app_settings.chunking
        self._admin_meta: dict[str, object] = {}

    def build_parents(self, document: ParsedDocument) -> list[ParentChunkDraft]:
        self._admin_meta = dict(getattr(document.metadata, "administrative_metadata", {}))
        drafts: list[ParentChunkDraft] = []

        def flush(heading_path: tuple[str, ...], nodes: list[ParsedNode]) -> None:
            body_texts = [normalize_text(node.text) for node in nodes]
            kept = [(node, text) for node, text in zip(nodes, body_texts, strict=True) if text]
            if not kept:
                return
            # L5: restore section-heading context lost when heading nodes were
            # excluded from bodies; the full path becomes the first segment.
            heading_segment = "\n".join(heading_path)
            texts = ([heading_segment] if heading_segment else []) + [text for _, text in kept]
            raw_text = "\n\n".join(texts)
            anchors: list[SegmentAnchor] = []
            if heading_segment:
                anchors.append((None, None, None))
            anchors.extend(_anchor_of(node) for node, _ in kept)
            page_start, page_end = _page_union([node for node, _ in kept])
            drafts.append(
                self._draft(
                    heading_path,
                    raw_text,
                    tuple(anchors),
                    len(drafts),
                )
            )

        hard_max = self._settings.parent_hard_max_tokens
        for heading_path, section_nodes in _iter_sections(document):
            current: list[ParsedNode] = []
            current_metrics: list[TextMetrics] = []

            for node in section_nodes:
                text = normalize_text(node.text)
                if not text or isinstance(node, HeadingNode):
                    continue

                extra = text_metrics(text)
                total = sum_metrics(current_metrics + [extra])
                size_tokens = estimate_from_metrics(total, joiners=len(current_metrics))

                if current and size_tokens > hard_max:
                    flush(heading_path, current)
                    current, current_metrics = [], []

                if not current and estimate_tokens(text) > hard_max:
                    block_anchor = _anchor_of(node)
                    for piece in self._split_oversized_node(text):
                        piece_text = normalize_text(piece)
                        if not piece_text:
                            continue
                        drafts.append(
                            self._draft(
                                heading_path,
                                piece_text,
                                (block_anchor,),
                                len(drafts),
                            )
                        )
                    continue

                current.append(node)
                current_metrics.append(extra)
            flush(heading_path, current)

        return drafts

    @staticmethod
    def _texts_of(nodes: list[ParsedNode]) -> list[str]:
        return [normalized for node in nodes if (normalized := normalize_text(node.text))]

    def _draft(
        self,
        heading_path: tuple[str, ...],
        raw_text: str,
        anchors: tuple[SegmentAnchor, ...],
        ordinal: int,
    ) -> ParentChunkDraft:
        context = self._context
        real_ids = [a[0] for a in anchors if a[0] is not None]
        chunk_id = parent_chunk_id(
            document_version_id=context.document_version_id,
            heading_path=heading_path,
            block_range=(real_ids[0] if real_ids else None, real_ids[-1] if real_ids else None),
            ordinal=ordinal,
        )
        return ParentChunkDraft(
            id=chunk_id,
            level=ChunkLevel.PARENT,
            document_id=context.document_id,
            document_version_id=context.document_version_id,
            source_id=context.source_id,
            title=context.title,
            heading_path=heading_path,
            ordinal=ordinal,
            raw_text=raw_text,
            token_count=estimate_tokens(raw_text),
            content_hash=content_hash(raw_text),
            source_block_ids=tuple(a[0] for a in anchors if a[0] is not None),
            page_start=_min_page(anchors),
            page_end=_max_page(anchors),
            parent_chunker_version=PARENT_CHUNKER_VERSION,
            segment_anchors=tuple(anchors),
            administrative_metadata=dict(self._admin_meta),
        )

    def _split_oversized_node(self, text: str) -> list[str]:
        """Ladder: sentence packing to target -> hard token cut as last resort."""
        target = self._settings.parent_target_tokens
        hard_max = self._settings.parent_hard_max_tokens
        pieces: list[str] = []
        buffer: list[str] = []
        buffer_tokens = 0
        for sentence in split_sentences(text):
            sentence_tokens = estimate_tokens(sentence)

            if sentence_tokens > hard_max:
                if buffer:
                    pieces.append("\n".join(buffer))
                    buffer, buffer_tokens = [], 0
                remaining = sentence
                while remaining and estimate_tokens(remaining) > hard_max:
                    # L3: slice the remainder with the pre-strip consumption count.
                    cut, consumed = truncate_at_token_boundary(remaining, target)
                    if not cut or consumed >= len(remaining):
                        cut, consumed = truncate_at_token_boundary(remaining, hard_max)
                    pieces.append(cut)
                    remaining = remaining[consumed:].strip()
                if remaining:
                    buffer, buffer_tokens = [remaining], estimate_tokens(remaining)
                continue

            if buffer_tokens + sentence_tokens > target and buffer:
                pieces.append("\n".join(buffer))
                buffer, buffer_tokens = [], 0
            buffer.append(sentence)
            buffer_tokens += sentence_tokens
        if buffer:
            pieces.append("\n".join(buffer))
        return [piece for piece in (normalize_text(p) for p in pieces) if piece]

    @staticmethod
    def _candidate_texts(nodes: list[ParsedNode]) -> list[str]:
        return SectionParentChunker._texts_of(nodes)


def _min_page(anchors: tuple[SegmentAnchor, ...]) -> int | None:
    pages = [a[1] for a in anchors if a[1] is not None]
    return min(pages) if pages else None


def _max_page(anchors: tuple[SegmentAnchor, ...]) -> int | None:
    pages = [a[2] for a in anchors if a[2] is not None]
    return max(pages) if pages else None


def _iter_sections(document: ParsedDocument) -> list[tuple[tuple[str, ...], list[ParsedNode]]]:
    """Consecutive same-heading nodes form one candidate section."""
    sections: list[tuple[tuple[str, ...], list[ParsedNode]]] = []
    for node in document.nodes:
        path = node.heading_path
        if sections and sections[-1][0] == path:
            sections[-1][1].append(node)
        else:
            sections.append((path, [node]))
    return sections
