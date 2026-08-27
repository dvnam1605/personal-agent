"""Document ingestion services (P9A+)."""

from app.services.ingestion.chunking import (
    ChunkContext,
    ChunkDraftSet,
    build_chunk_drafts,
)
from app.services.ingestion.fingerprint import compute_fingerprint
from app.services.ingestion.parsing import (
    DoclingDocumentParser,
    DocumentParser,
    MarkdownDocumentParser,
    ParsedDocument,
    ParseResult,
    ParseStatus,
    evaluate_parse_quality,
    parse_source,
)
from app.services.ingestion.source import checksum_bytes, checksum_file
from app.services.ingestion.type_detection import detect_document_type
from app.services.ingestion.versioning import decide_ingest_action

__all__ = [
    "ChunkContext",
    "ChunkDraftSet",
    "DoclingDocumentParser",
    "DocumentParser",
    "MarkdownDocumentParser",
    "ParsedDocument",
    "ParseResult",
    "ParseStatus",
    "build_chunk_drafts",
    "checksum_bytes",
    "checksum_file",
    "compute_fingerprint",
    "decide_ingest_action",
    "detect_document_type",
    "evaluate_parse_quality",
    "parse_source",
]
