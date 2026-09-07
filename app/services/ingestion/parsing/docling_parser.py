"""DoclingDocumentParser: born-digital PDF + DOCX via Docling (spec P9B-1).

Docling is imported lazily inside this module only; nothing outside the
parsing package ever sees a Docling type. The primary parser never runs OCR
(``do_ocr=False``): scanned PDFs fail the useful-text gate and come back as
typed NEEDS_OCR results instead (spec P9B-4).
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import io
import threading
from typing import TYPE_CHECKING

from app.domain.models.documents import SourceDocument
from app.domain.models.parsed_document import (
    HeadingNode,
    ListNode,
    ParagraphNode,
    ParsedDocument,
    ParsedDocumentMetadata,
    SourceAnchor,
    TableNode,
)
from app.services.ingestion.parsing.base import DocumentParseError, ParserUnavailableError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docling.document_converter import DocumentConverter

DOCLING_PARSER_NAME = "docling"
_DOCLING_DISTRIBUTION = "docling"

_converter: DocumentConverter | None = None
_converter_lock = threading.Lock()


def _load_converter() -> DocumentConverter:
    """Build (once) a DocumentConverter with OCR disabled for PDFs."""
    global _converter
    if _converter is not None:
        return _converter
    with _converter_lock:
        if _converter is not None:
            return _converter
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as exc:  # pragma: no cover - exercised without docling installed
            raise ParserUnavailableError(
                "docling is not installed; add it to the environment to parse PDF/DOCX"
            ) from exc

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        return _converter


def docling_version() -> str:
    """Public runtime version of the docling engine (used by fingerprints)."""
    try:
        return importlib.metadata.version(_DOCLING_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - defensive
        return "unknown"


def _page_range(item: object) -> tuple[int | None, int | None]:
    prov = getattr(item, "prov", None) or []
    page_numbers = [entry.page_no for entry in prov if getattr(entry, "page_no", None)]
    if not page_numbers:
        return None, None
    return min(page_numbers), max(page_numbers)


class DoclingDocumentParser:
    """Converts PDF/DOCX bytes into the normalized document tree."""

    async def parse(self, source: SourceDocument, content: bytes) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, source, content)

    def _parse_sync(self, source: SourceDocument, content: bytes) -> ParsedDocument:
        from docling_core.types.doc.items.picture.picture import PictureItem
        from docling_core.types.doc.items.table.table import TableItem
        from docling_core.types.doc.items.text import (
            ListItem,
            SectionHeaderItem,
            TextItem,
            TitleItem,
        )
        from docling_core.types.io import DocumentStream

        converter = _load_converter()
        try:
            result = converter.convert(
                DocumentStream(name=source.filename, stream=io.BytesIO(content)),
                raises_on_error=True,
            )
        except Exception as exc:
            raise DocumentParseError(f"docling failed on {source.filename}: {exc}") from exc

        document = result.document
        nodes: list[HeadingNode | ListNode | ParagraphNode | TableNode] = []
        heading_stack: list[tuple[int, str]] = []
        warnings: list[str] = []
        pages_seen: set[int] = set()

        def heading_path() -> tuple[str, ...]:
            return tuple(title for _, title in heading_stack)

        for block_index, item in enumerate(document.iterate_items()):
            node_item = item[0] if isinstance(item, tuple) else item
            page_start, page_end = _page_range(node_item)
            if page_start is not None:
                pages_seen.update(range(page_start, (page_end or page_start) + 1))
                anchor = SourceAnchor(
                    block_index=block_index, page_start=page_start, page_end=page_end
                )
            else:
                anchor = SourceAnchor(block_index=block_index)

            if isinstance(node_item, TitleItem):
                title_text = (node_item.text or "").strip()
                nodes.append(
                    HeadingNode(
                        node_id=f"n{len(nodes):04d}",
                        order=block_index,
                        text=title_text,
                        heading_path=heading_path(),
                        anchor=anchor,
                        level=1,
                    )
                )
                # A title is the document root: it is never popped by later
                # sections, only replaced by another title.
                heading_stack[:] = [(0, title_text)]
            elif isinstance(node_item, SectionHeaderItem):
                level = min(max(int(node_item.level or 2), 1), 6)
                title = (node_item.text or "").strip()
                nodes.append(
                    HeadingNode(
                        node_id=f"n{len(nodes):04d}",
                        order=block_index,
                        text=title,
                        heading_path=heading_path(),
                        anchor=anchor,
                        level=level,
                    )
                )
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))
            elif isinstance(node_item, ListItem):
                text = (getattr(node_item, "text", "") or "").strip()
                previous = nodes[-1] if nodes else None
                if isinstance(previous, ListNode):
                    updated_items = (*previous.items, text)
                    nodes[-1] = ListNode(
                        node_id=previous.node_id,
                        order=previous.order,
                        text="\n".join(updated_items),
                        heading_path=previous.heading_path,
                        anchor=previous.anchor,
                        items=updated_items,
                    )
                    continue
                nodes.append(
                    ListNode(
                        node_id=f"n{len(nodes):04d}",
                        order=block_index,
                        text=text,
                        heading_path=heading_path(),
                        anchor=anchor,
                        items=(text,) if text else (),
                    )
                )
            elif isinstance(node_item, TableItem):
                markdown = node_item.export_to_markdown(document)
                table_data = getattr(node_item, "data", None)
                row_count = int(getattr(table_data, "num_rows", 0) or 0)
                column_count = int(getattr(table_data, "num_cols", 0) or 0)
                nodes.append(
                    TableNode(
                        node_id=f"n{len(nodes):04d}",
                        order=block_index,
                        text=markdown,
                        heading_path=heading_path(),
                        anchor=anchor,
                        markdown=markdown,
                        row_count=row_count,
                        column_count=column_count,
                    )
                )
            elif isinstance(node_item, TextItem):
                text = (node_item.text or "").strip()
                if not text:
                    continue
                item_label = str(getattr(node_item.label, "value", node_item.label) or "")
                if item_label == "title":
                    nodes.append(
                        HeadingNode(
                            node_id=f"n{len(nodes):04d}",
                            order=block_index,
                            text=text,
                            heading_path=heading_path(),
                            anchor=anchor,
                            level=1,
                        )
                    )
                    heading_stack[:] = [(0, text)]
                    continue
                if item_label == "section_header":
                    level = min(max(int(getattr(node_item, "level", 0) or 0) or 2, 1), 6)
                    nodes.append(
                        HeadingNode(
                            node_id=f"n{len(nodes):04d}",
                            order=block_index,
                            text=text,
                            heading_path=heading_path(),
                            anchor=anchor,
                            level=level,
                        )
                    )
                    while heading_stack and heading_stack[-1][0] >= level:
                        heading_stack.pop()
                    heading_stack.append((level, text))
                    continue
                nodes.append(
                    ParagraphNode(
                        node_id=f"n{len(nodes):04d}",
                        order=block_index,
                        text=text,
                        heading_path=heading_path(),
                        anchor=anchor,
                    )
                )
            elif isinstance(node_item, PictureItem):
                pic_text = (getattr(node_item, "text", "") or "").strip()
                pic_caption = ""
                captions = getattr(node_item, "captions", None)
                if captions:
                    pic_caption = " ".join(
                        getattr(c, "text", str(c)) for c in captions if getattr(c, "text", None)
                    ).strip()
                parts = [p for p in (pic_caption, pic_text) if p]
                content = " - ".join(parts)
                if content:
                    nodes.append(
                        ParagraphNode(
                            node_id=f"n{len(nodes):04d}",
                            order=block_index,
                            text=f"[Hình ảnh: {content}]",
                            heading_path=heading_path(),
                            anchor=anchor,
                        )
                    )
                elif "pictures are not represented in the V1 tree" not in warnings:
                    warnings.append("pictures are not represented in the V1 tree")
            else:
                label = type(node_item).__name__
                if f"unhandled docling item skipped: {label}" not in warnings:
                    warnings.append(f"unhandled docling item skipped: {label}")

        partial_status = str(getattr(result.status, "value", result.status) or "")
        if partial_status == "partial_success" and result.errors:
            warnings.extend(str(error) for error in result.errors)

        page_count = len(getattr(document, "pages", {}) or {})
        empty_pages = max(page_count - len(pages_seen), 0)
        if empty_pages:
            warnings.append(f"{empty_pages} page(s) contributed no content")

        full_text = " ".join(n.text for n in nodes)
        from app.services.ingestion.administrative_extractor import AdministrativeMetadataExtractor

        admin_meta = AdministrativeMetadataExtractor.extract(full_text, filename=source.filename)

        return ParsedDocument(
            metadata=ParsedDocumentMetadata(
                source_id=source.source_id,
                filename=source.filename,
                parser_name=DOCLING_PARSER_NAME,
                parser_version=docling_version(),
                page_count=page_count,
                administrative_metadata=admin_meta.to_dict(),
                parse_warnings=tuple(warnings),
            ),
            nodes=tuple(nodes),
        )
