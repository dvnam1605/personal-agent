"""Shared helpers for chunking-engine unit tests (spec P9C)."""

from __future__ import annotations

from typing import Any

from app.domain.models.chunks import ChildChunkDraft, ChunkLevel, ParentChunkDraft
from app.domain.models.parsed_document import (
    HeadingNode,
    ListNode,
    ParagraphNode,
    ParsedDocument,
    ParsedDocumentMetadata,
    SourceAnchor,
    TableNode,
)
from app.services.ingestion.chunking.engine import build_chunk_drafts
from app.services.ingestion.chunking.protocols import ChunkContext


def metadata(**overrides: Any) -> ParsedDocumentMetadata:
    values: dict[str, Any] = {
        "source_id": "src-1",
        "filename": "doc.pdf",
        "parser_name": "docling",
        "parser_version": "2.121.0",
    }
    values.update(overrides)
    return ParsedDocumentMetadata(**values)


def context(**overrides: Any) -> ChunkContext:
    values: dict[str, Any] = {
        "document_id": "doc-1",
        "document_version_id": "ver-1",
        "source_id": "src-1",
        "title": "Test Document",
        "filename": "doc.pdf",
        "mime_type": "application/pdf",
        "source_type": "upload",
    }
    values.update(overrides)
    return ChunkContext(**values)


def document(nodes: list[Any], **overrides: Any) -> ParsedDocument:
    return ParsedDocument(metadata=metadata(**overrides), nodes=tuple(nodes))


def heading(text: str, level: int = 1, order: int = 0) -> HeadingNode:
    return HeadingNode(node_id=f"n{order:04d}", order=order, text=text, level=level)


def paragraph(
    text: str,
    order: int = 1,
    heading_path: tuple[str, ...] = (),
    page: int | None = None,
) -> ParagraphNode:
    anchor = (
        SourceAnchor(block_index=order, page_start=page, page_end=page)
        if page
        else SourceAnchor(block_index=order)
    )
    return ParagraphNode(
        node_id=f"n{order:04d}", order=order, text=text, heading_path=heading_path, anchor=anchor
    )


def list_node(
    items: tuple[str, ...], order: int = 2, heading_path: tuple[str, ...] = ()
) -> ListNode:
    return ListNode(
        node_id=f"n{order:04d}",
        order=order,
        text="\n".join(items),
        items=items,
        heading_path=heading_path,
    )


def table(
    markdown: str, order: int = 3, heading_path: tuple[str, ...] = (), rows: int = 2, cols: int = 2
) -> TableNode:
    return TableNode(
        node_id=f"n{order:04d}",
        order=order,
        text=markdown,
        heading_path=heading_path,
        markdown=markdown,
        row_count=rows,
        column_count=cols,
    )


def chunk(document_tree: ParsedDocument, ctx: ChunkContext | None = None, **kwargs: Any):
    return build_chunk_drafts(document_tree, ctx or context(), **kwargs)


def parent_ids(parents: list[ParentChunkDraft] | tuple[ParentChunkDraft, ...]) -> set[str]:
    return {parent.id for parent in parents}


def children_of(
    children: list[ChildChunkDraft] | tuple[ChildChunkDraft, ...], parent: ParentChunkDraft
) -> list[ChildChunkDraft]:
    return [child for child in children if child.parent_id == parent.id]


def levels(children: list[ChildChunkDraft]) -> list[ChunkLevel]:
    return [child.level for child in children]
