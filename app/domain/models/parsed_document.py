"""Provider-neutral normalized document tree and parse-quality contracts (P9B).

These types are the stable boundary between the parsing layer and everything
above it (chunking P9C, orchestration P9D). Docling/OCR-specific objects never
cross this boundary (spec P9B-1/P9B-2).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeType(StrEnum):
    """Kinds of nodes in the canonical normalized document tree."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"


class SourceAnchor(BaseModel):
    """Provider-neutral provenance for one node.

    PDFs carry real page numbers; DOCX page numbers may be unavailable or
    unstable, in which case heading-path provenance is the required signal.
    """

    model_config = ConfigDict(frozen=True)

    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    block_index: int | None = Field(default=None, ge=0)


class ParsedNode(BaseModel):
    """Fields shared by every node of the normalized tree."""

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(min_length=1)
    order: int = Field(ge=0)
    text: str = ""
    heading_path: tuple[str, ...] = ()
    anchor: SourceAnchor | None = None


class HeadingNode(ParsedNode):
    node_type: Literal[NodeType.HEADING] = NodeType.HEADING
    level: int = Field(ge=1, le=6)


class ParagraphNode(ParsedNode):
    node_type: Literal[NodeType.PARAGRAPH] = NodeType.PARAGRAPH


class ListNode(ParsedNode):
    node_type: Literal[NodeType.LIST] = NodeType.LIST
    items: tuple[str, ...] = ()

    @model_validator(mode="after")
    def items_or_text_present(self) -> ListNode:
        if not self.items and not self.text.strip():
            raise ValueError("ListNode requires at least one item or text")
        return self


class TableNode(ParsedNode):
    node_type: Literal[NodeType.TABLE] = NodeType.TABLE
    markdown: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)


DocumentNode = Annotated[
    HeadingNode | ParagraphNode | ListNode | TableNode,
    Field(discriminator="node_type"),
]


class ParsedDocumentMetadata(BaseModel):
    """Provenance about how a ParsedDocument was produced."""

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    ocr_used: bool = False
    page_count: int | None = Field(default=None, ge=0)
    parse_warnings: tuple[str, ...] = ()
    administrative_metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    """Root of the provider-independent normalized document tree."""

    model_config = ConfigDict(frozen=True)

    metadata: ParsedDocumentMetadata
    nodes: tuple[DocumentNode, ...] = ()


class ParseStatus(StrEnum):
    """Typed parse outcomes; broken parses never abort a batch (spec P9B-3)."""

    PARSED = "PARSED"
    NEEDS_OCR = "NEEDS_OCR"
    CORRUPT = "CORRUPT"
    UNSUPPORTED = "UNSUPPORTED"


class ParseQualityReport(BaseModel):
    """Quality metrics captured before chunking (spec P9B-3)."""

    model_config = ConfigDict(frozen=True)

    text_length: int = Field(ge=0)
    node_count: int = Field(ge=0)
    heading_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    empty_page_count: int = Field(default=0, ge=0)
    ocr_used: bool = False
    parse_warnings: tuple[str, ...] = ()


class ParseResult(BaseModel):
    """Outcome of parsing one source through the pipeline entry point."""

    model_config = ConfigDict(frozen=True)

    status: ParseStatus
    source_id: str = Field(min_length=1)
    document: ParsedDocument | None = None
    quality: ParseQualityReport | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def check_status_invariants(self) -> ParseResult:
        if self.status == ParseStatus.PARSED:
            if self.document is None or self.quality is None:
                raise ValueError("PARSED results require document and quality")
        if self.status == ParseStatus.UNSUPPORTED and self.failure_reason is None:
            raise ValueError("UNSUPPORTED results require failure_reason")
        return self
