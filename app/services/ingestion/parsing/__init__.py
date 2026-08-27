"""P9B parsing layer: provider-neutral parsing behind a DocumentParser Protocol.

Docling-specific objects never leave this package; callers only ever see
ParsedDocument / ParseResult domain models.
"""

from app.domain.models.parsed_document import (
    DocumentNode,
    HeadingNode,
    ListNode,
    ParagraphNode,
    ParsedDocument,
    ParsedDocumentMetadata,
    ParseQualityReport,
    ParseResult,
    ParseStatus,
    SourceAnchor,
    TableNode,
)
from app.services.ingestion.parsing.base import (
    DocumentParseError,
    DocumentParser,
    ParserUnavailableError,
    build_parser_for_type,
    parse_source,
)
from app.services.ingestion.parsing.docling_parser import DoclingDocumentParser, docling_version
from app.services.ingestion.parsing.markdown_parser import MarkdownDocumentParser
from app.services.ingestion.parsing.quality import evaluate_parse_quality

__all__ = [
    "DoclingDocumentParser",
    "DocumentNode",
    "DocumentParseError",
    "DocumentParser",
    "HeadingNode",
    "ListNode",
    "MarkdownDocumentParser",
    "ParagraphNode",
    "ParsedDocument",
    "ParsedDocumentMetadata",
    "ParseQualityReport",
    "ParseResult",
    "ParseStatus",
    "ParserUnavailableError",
    "SourceAnchor",
    "TableNode",
    "build_parser_for_type",
    "docling_version",
    "evaluate_parse_quality",
    "parse_source",
]
