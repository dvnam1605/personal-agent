"""MarkdownDocumentParser: preparsed_markdown / .md sources (spec P9B-1).

Pure-Python line-based parsing into the canonical tree. This is the shape the
offline OCR batch script (P9E) feeds back into the pipeline, so headings and
table structure must survive intact.
"""

from __future__ import annotations

import re

from app.domain.models.ingestion.documents import SourceDocument
from app.domain.models.ingestion.parsed_document import (
    HeadingNode,
    ListNode,
    ParagraphNode,
    ParsedDocument,
    ParsedDocumentMetadata,
    SourceAnchor,
    TableNode,
)
from app.services.ingestion.administrative_extractor import AdministrativeMetadataExtractor
from app.services.ingestion.parsing.base import DocumentParseError

MARKDOWN_PARSER_NAME = "markdown"
MARKDOWN_PARSER_VERSION = "1.0.0"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _anchor(block_index: int) -> SourceAnchor:
    return SourceAnchor(block_index=block_index)


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and stripped.count("|") >= 2


class MarkdownDocumentParser:
    """Parses Markdown bytes into the normalized document tree."""

    async def parse(self, source: SourceDocument, content: bytes) -> ParsedDocument:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentParseError(f"markdown is not valid utf-8: {source.filename}") from exc

        nodes: list[HeadingNode | ListNode | ParagraphNode | TableNode] = []
        heading_stack: list[tuple[int, str]] = []
        block_index = 0
        parse_warnings: list[str] = []

        lines = text.splitlines()
        i = 0
        in_fence = False
        fence_buffer: list[str] = []
        while i < len(lines):
            line = lines[i]

            if _FENCE_RE.match(line):
                if not in_fence:
                    in_fence = True
                    fence_buffer = [line.strip()]
                else:
                    fence_buffer.append(line.strip())
                    nodes.append(
                        ParagraphNode(
                            node_id=f"n{len(nodes):04d}",
                            order=block_index,
                            text="\n".join(fence_buffer),
                            heading_path=tuple(title for _, title in heading_stack),
                            anchor=_anchor(block_index),
                        )
                    )
                    block_index += 1
                    in_fence = False
                i += 1
                continue
            if in_fence:
                fence_buffer.append(line)
                i += 1
                continue

            heading_match = _HEADING_RE.match(line)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                nodes.append(
                    HeadingNode(
                        node_id=f"n{len(nodes):04d}",
                        order=block_index,
                        text=title,
                        heading_path=tuple(title for _, title in heading_stack),
                        anchor=_anchor(block_index),
                        level=level,
                    )
                )
                heading_stack.append((level, title))
                block_index += 1
                i += 1
                continue

            if (
                _looks_like_table_row(line)
                and i + 1 < len(lines)
                and _TABLE_SEPARATOR_RE.match(lines[i + 1])
            ):
                table_lines = [line]
                j = i + 1
                while j < len(lines) and _looks_like_table_row(lines[j]):
                    table_lines.append(lines[j])
                    j += 1
                rows = [
                    [cell.strip() for cell in row.strip().strip("|").split("|")]
                    for row in table_lines
                    if not _TABLE_SEPARATOR_RE.match(row)
                ]
                column_count = max((len(row) for row in rows), default=0)
                nodes.append(
                    TableNode(
                        node_id=f"n{len(nodes):04d}",
                        order=block_index,
                        text="\n".join(table_lines),
                        heading_path=tuple(title for _, title in heading_stack),
                        anchor=_anchor(block_index),
                        markdown="\n".join(table_lines),
                        row_count=max(len(rows) - 1, 0),
                        column_count=column_count,
                    )
                )
                block_index += 1
                i = j
                continue

            list_match = _LIST_ITEM_RE.match(line)
            if list_match:
                items: list[str] = []
                while i < len(lines):
                    item_match = _LIST_ITEM_RE.match(lines[i])
                    if not item_match:
                        break
                    items.append(item_match.group(1).strip())
                    i += 1
                nodes.append(
                    ListNode(
                        node_id=f"n{len(nodes):04d}",
                        order=block_index,
                        text="\n".join(items),
                        heading_path=tuple(title for _, title in heading_stack),
                        anchor=_anchor(block_index),
                        items=tuple(items),
                    )
                )
                block_index += 1
                continue

            if line.strip():
                paragraph_lines = [line]
                j = i + 1
                while j < len(lines) and lines[j].strip():
                    if (
                        _HEADING_RE.match(lines[j])
                        or _LIST_ITEM_RE.match(lines[j])
                        or _FENCE_RE.match(lines[j])
                        or (
                            _looks_like_table_row(lines[j])
                            and j + 1 < len(lines)
                            and _TABLE_SEPARATOR_RE.match(lines[j + 1])
                        )
                    ):
                        break
                    paragraph_lines.append(lines[j])
                    j += 1
                nodes.append(
                    ParagraphNode(
                        node_id=f"n{len(nodes):04d}",
                        order=block_index,
                        text=" ".join(part.strip() for part in paragraph_lines),
                        heading_path=tuple(title for _, title in heading_stack),
                        anchor=_anchor(block_index),
                    )
                )
                block_index += 1
                i = j
                continue

            i += 1

        if in_fence and fence_buffer:
            nodes.append(
                ParagraphNode(
                    node_id=f"n{len(nodes):04d}",
                    order=block_index,
                    text="\n".join(fence_buffer),
                    heading_path=tuple(title for _, title in heading_stack),
                    anchor=_anchor(block_index),
                )
            )
            block_index += 1
            parse_warnings.append("unclosed code fence flushed as paragraph at EOF")

        admin_meta = AdministrativeMetadataExtractor.extract(text, filename=source.filename)

        return ParsedDocument(
            metadata=ParsedDocumentMetadata(
                source_id=source.source_id,
                filename=source.filename,
                parser_name=MARKDOWN_PARSER_NAME,
                parser_version=MARKDOWN_PARSER_VERSION,
                administrative_metadata=admin_meta.to_dict(),
                parse_warnings=tuple(parse_warnings),
            ),
            nodes=tuple(nodes),
        )
