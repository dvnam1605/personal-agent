"""PyMuPDFDocumentParser: fast, low-memory parser for text-layer PDFs.

Docling runs layout and table models per page, which is accurate but needs
gigabytes of RAM for a book-length PDF. A born-digital PDF already carries
its text, so this parser reads it directly with PyMuPDF:

* headings come from the PDF outline (bookmarks) when it has one, matched
  to the line where each section starts; otherwise from font size / bold;
* running headers, footers and page numbers repeated across pages are
  dropped; hyphenated line breaks are re-joined;
* ruled tables are detected with ``find_tables`` and kept as Markdown;
* every node is anchored to its page.

Scanned or image-only PDFs never reach this parser: ``prefer_pymupdf``
routes them to Docling so they still come back as NEEDS_OCR.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.core.text import unaccent_vietnamese
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
from app.services.ingestion.parsing.base import DocumentParseError, ParserUnavailableError

PYMUPDF_PARSER_NAME = "pymupdf"
_PYMUPDF_DISTRIBUTION = "pymupdf"

# A page "has text" above this many characters; a PDF is text-layer when at
# least this share of its pages do.
_MIN_PAGE_CHARS = 30
_MIN_TEXT_PAGE_RATIO = 0.5
# Running header/footer: a line in the top/bottom margin repeated on at
# least this share of pages.
_MARGIN_RATIO = 0.07
_REPEAT_RATIO = 0.3

_BULLET = re.compile(r"^\s*(?:[•●▪◦‣∙·\-–—*]|\(?\d{1,2}[.)]|\(?[a-zđ][.)])\s+", re.IGNORECASE)
# Table-of-contents dot leaders: "About This Book . . . . . . 3".
_LEADER = re.compile(r"(?:\.\s?){6,}")
_PAGE_NUMBER = re.compile(r"^\s*(?:trang\s+|page\s+)?\d{1,4}(?:\s*/\s*\d{1,4})?\s*$", re.I)
# "Chapter 3", "Part II:", "1.2.1", "IV." — roman numerals only in capitals so
# words like "Introduction" keep their first letter.
_LEADING_NUMBER = re.compile(
    r"^(?:(?:chapter|chuong|part|phan|muc|section)\s+)?"
    r"(?:(?-i:[IVXLC]+)(?=[.:)\s]|$)|\d+(?:\.\d+)*)[.:)]?(?:\s+|$)",
    re.I,
)


def pymupdf_version() -> str:
    try:
        return importlib.metadata.version(_PYMUPDF_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _import_pymupdf() -> Any:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - exercised without pymupdf installed
        raise ParserUnavailableError("pymupdf is not installed") from exc
    return pymupdf


@dataclass(frozen=True)
class PdfTextStats:
    page_count: int
    text_pages: int
    glued_ratio: float = 0.0

    @property
    def has_text_layer(self) -> bool:
        return self.page_count > 0 and self.text_pages / self.page_count >= _MIN_TEXT_PAGE_RATIO

    @property
    def text_is_clean(self) -> bool:
        return self.glued_ratio < _MAX_GLUED_RATIO


_VI_TONED = "áàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
_VI_VOWELS = "aăâeêioôơuưy" + _VI_TONED
_VI_MARKED = re.compile(f"[ăâđêôơư{_VI_TONED}]")
_VOWEL_RUN = re.compile(f"[{_VI_VOWELS}]+")
_WORD = re.compile(r"[^\W\d_]+")
# Some PDFs (fonts with odd glyph widths) lose the spaces between Vietnamese
# syllables in the text layer ("hệthống"); Docling's own PDF backend reads
# those correctly, so such files stay on Docling.
_MAX_GLUED_RATIO = 0.05
_MIN_VI_WORDS = 200


def vietnamese_glued_ratio(text: str) -> float:
    """Share of Vietnamese words holding more than one syllable (spaces lost)."""
    words = [w for w in _WORD.findall(text.lower()) if _VI_MARKED.search(w)]
    if len(words) < _MIN_VI_WORDS:
        return 0.0
    glued = sum(1 for w in words if len(_VOWEL_RUN.findall(w)) >= 2)
    return glued / len(words)


def pdf_text_stats(content: bytes) -> PdfTextStats | None:
    """Page count and pages with a text layer; None when unreadable."""
    try:
        pymupdf = _import_pymupdf()
        with pymupdf.open(stream=content, filetype="pdf") as doc:
            texts = [page.get_text() for page in doc]
            return PdfTextStats(
                page_count=doc.page_count,
                text_pages=sum(1 for t in texts if len(t.strip()) >= _MIN_PAGE_CHARS),
                glued_ratio=vietnamese_glued_ratio(" ".join(texts)),
            )
    except Exception:  # noqa: BLE001 - Docling decides what to do with a broken file
        return None


def prefer_pymupdf(content: bytes, *, engine: str, min_pages: int) -> bool:
    """Whether a PDF should be parsed by PyMuPDF instead of Docling.

    ``engine``: "docling" never, "pymupdf" whenever the PDF has a text
    layer, "auto" when it has one and is long enough (>= ``min_pages``) that
    Docling's memory cost matters; short administrative PDFs keep Docling.
    Scanned PDFs and PDFs whose text layer lost word spacing stay on Docling.
    """
    if engine == "docling":
        return False
    stats = pdf_text_stats(content)
    if stats is None or not stats.has_text_layer or not stats.text_is_clean:
        return False
    return engine == "pymupdf" or stats.page_count >= min_pages


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", unaccent_vietnamese(text).lower())


def _norm_unnumbered(text: str) -> str:
    return _norm(_LEADING_NUMBER.sub("", unaccent_vietnamese(text).strip(), count=1))


@dataclass
class _Line:
    text: str
    size: float
    bold: bool
    bbox: tuple[float, float, float, float]


@dataclass
class _Block:
    lines: list[_Line]
    bbox: tuple[float, float, float, float]

    @property
    def text(self) -> str:
        return _join_lines([ln.text for ln in self.lines])

    @property
    def size(self) -> float:
        weights = [(ln.size, len(ln.text)) for ln in self.lines if ln.text.strip()]
        total = sum(w for _, w in weights)
        return sum(s * w for s, w in weights) / total if total else 0.0

    @property
    def bold(self) -> bool:
        return bool(self.lines) and all(ln.bold for ln in self.lines if ln.text.strip())


@dataclass
class _TocEntry:
    level: int
    title: str
    norm: str
    page: int  # 0-based
    done: bool = False


@dataclass
class _Builder:
    nodes: list[HeadingNode | ListNode | ParagraphNode | TableNode] = field(default_factory=list)
    stack: list[tuple[int, str]] = field(default_factory=list)

    def path(self) -> tuple[str, ...]:
        return tuple(title for _, title in self.stack)

    def _id(self) -> str:
        return f"n{len(self.nodes):04d}"

    def heading(self, text: str, level: int, anchor: SourceAnchor) -> None:
        level = min(max(level, 1), 6)
        self.nodes.append(
            HeadingNode(
                node_id=self._id(),
                order=len(self.nodes),
                text=text,
                heading_path=self.path(),
                anchor=anchor,
                level=level,
            )
        )
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, text))

    def paragraph(self, text: str, anchor: SourceAnchor) -> None:
        self.nodes.append(
            ParagraphNode(
                node_id=self._id(),
                order=len(self.nodes),
                text=text,
                heading_path=self.path(),
                anchor=anchor,
            )
        )

    def list_items(self, items: list[str], anchor: SourceAnchor) -> None:
        previous = self.nodes[-1] if self.nodes else None
        if isinstance(previous, ListNode) and previous.heading_path == self.path():
            merged = (*previous.items, *items)
            self.nodes[-1] = previous.model_copy(
                update={"items": merged, "text": "\n".join(merged)}
            )
            return
        self.nodes.append(
            ListNode(
                node_id=self._id(),
                order=len(self.nodes),
                text="\n".join(items),
                heading_path=self.path(),
                anchor=anchor,
                items=tuple(items),
            )
        )

    def table(self, markdown: str, rows: int, cols: int, anchor: SourceAnchor) -> None:
        self.nodes.append(
            TableNode(
                node_id=self._id(),
                order=len(self.nodes),
                text=markdown,
                heading_path=self.path(),
                anchor=anchor,
                markdown=markdown,
                row_count=rows,
                column_count=cols,
            )
        )


def _join_lines(lines: list[str]) -> str:
    """Join wrapped lines; re-join words hyphenated across a line break."""
    out = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("-") and line[:1].islower():
            out = out[:-1] + line
        else:
            out = f"{out} {line}"
    return re.sub(r"\s+", " ", out).strip()


def _blocks_of(page: Any) -> list[_Block]:
    blocks: list[_Block] = []
    for raw in page.get_text("dict", sort=True)["blocks"]:
        if raw.get("type") != 0:
            continue
        lines: list[_Line] = []
        for line in raw.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in line["spans"]).strip()
            size = max(spans, key=lambda s: len(s["text"]))["size"]
            bold = all(
                int(s.get("flags", 0)) & 16 or "bold" in s.get("font", "").lower() for s in spans
            )
            lines.append(
                _Line(
                    text=text, size=round(float(size), 1), bold=bool(bold), bbox=tuple(line["bbox"])
                )
            )
        if lines:
            blocks.append(_Block(lines=lines, bbox=tuple(raw["bbox"])))
    return blocks


def _margin_key(text: str) -> str:
    """Page-independent form of a margin line; empty for bare numbers.

    Bare numbers (page or section numbers) are not running text: page
    numbers are dropped by ``_PAGE_NUMBER``, section numbers must survive.
    """
    key = re.sub(r"\d+", "#", _norm(text)) if text else ""
    return key if len(key.replace("#", "")) >= 4 else ""


def _running_lines(pages: list[tuple[float, list[_Block]]]) -> set[str]:
    """Normalized texts of lines repeated in the page margins (headers/footers)."""
    if len(pages) < 4:
        return set()
    counts: Counter[str] = Counter()
    for height, blocks in pages:
        seen: set[str] = set()
        for block in blocks:
            for line in block.lines:
                top, bottom = line.bbox[1], line.bbox[3]
                if top < height * _MARGIN_RATIO or bottom > height * (1 - _MARGIN_RATIO):
                    key = _margin_key(line.text)
                    if key and key not in seen:
                        seen.add(key)
                        counts[key] += 1
    threshold = max(3, int(len(pages) * _REPEAT_RATIO))
    return {key for key, n in counts.items() if n >= threshold}


def _inside(inner: tuple[float, ...], outer: tuple[float, ...]) -> bool:
    x0, y0, x1, y1 = inner
    ox0, oy0, ox1, oy1 = outer
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return ox0 <= cx <= ox1 and oy0 <= cy <= oy1


def _font_levels(pages: list[tuple[float, list[_Block]]]) -> tuple[float, dict[float, int]]:
    """Body font size and heading level for each larger font size."""
    sizes: Counter[float] = Counter()
    for _, blocks in pages:
        for block in blocks:
            for line in block.lines:
                sizes[line.size] += len(line.text)
    if not sizes:
        return 0.0, {}
    body = sizes.most_common(1)[0][0]
    larger = sorted({s for s in sizes if s >= body * 1.15}, reverse=True)
    return body, {s: min(i + 1, 4) for i, s in enumerate(larger)}


class PyMuPDFDocumentParser:
    """Converts a text-layer PDF into the normalized document tree."""

    def __init__(self, *, detect_tables: bool = True) -> None:
        self._detect_tables = detect_tables

    async def parse(self, source: SourceDocument, content: bytes) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, source, content)

    def _parse_sync(self, source: SourceDocument, content: bytes) -> ParsedDocument:
        pymupdf = _import_pymupdf()
        try:
            doc = pymupdf.open(stream=content, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise DocumentParseError(f"pymupdf failed on {source.filename}: {exc}") from exc
        with doc:
            return self._convert(doc, source)

    def _convert(self, doc: Any, source: SourceDocument) -> ParsedDocument:
        warnings: list[str] = []
        pages: list[tuple[float, list[_Block]]] = []
        tables: list[list[tuple[tuple[float, ...], str, int, int]]] = []
        image_pages = 0
        for page in doc:
            blocks = _blocks_of(page)
            pages.append((float(page.rect.height), blocks))
            if page.get_images(full=False):
                image_pages += 1
            tables.append(self._tables_of(page) if self._detect_tables else [])

        toc = [
            _TocEntry(level=lvl, title=title.strip(), norm=_norm_unnumbered(title), page=p - 1)
            for lvl, title, p, *_ in doc.get_toc(simple=True)
            if title and title.strip() and p >= 1
        ]
        use_toc = len(toc) >= 3
        running = _running_lines(pages)
        body, levels = _font_levels(pages)
        builder = _Builder()
        empty_pages = 0

        for pno, (_height, blocks) in enumerate(pages):
            before = page_start = len(builder.nodes)
            page_tables = list(tables[pno])
            anchor = SourceAnchor(page_start=pno + 1, page_end=pno + 1, block_index=pno)
            page_toc = [e for e in toc if e.page == pno and not e.done] if use_toc else []
            consumed_suffix = ""
            number_lines: list[_Line] = []  # a section number set apart from its title
            for block in blocks:
                block_lines = [
                    ln
                    for ln in block.lines
                    if _margin_key(ln.text) not in running
                    and not _PAGE_NUMBER.match(ln.text)
                    and not _LEADER.search(ln.text)
                ]
                if not block_lines:
                    continue
                block = _Block(lines=block_lines, bbox=block.bbox)
                # Tables are emitted once, at the first block inside them.
                inside = next((t for t in page_tables if _inside(block.bbox, t[0])), None)
                if inside is not None:
                    builder.table(inside[1], inside[2], inside[3], anchor)
                    page_tables.remove(inside)
                    continue
                if any(_inside(block.bbox, t[0]) for t in tables[pno]):
                    continue
                if number_lines:
                    block_lines = number_lines + block_lines
                    block = _Block(lines=block_lines, bbox=block.bbox)
                    number_lines = []
                text = block.text
                if not text:
                    continue
                if use_toc and page_toc and _LEADING_NUMBER.fullmatch(text):
                    number_lines = block_lines
                    continue
                if consumed_suffix:
                    # Second half of a heading split over two blocks.
                    if _norm(text) and consumed_suffix.startswith(_norm(text)):
                        consumed_suffix = consumed_suffix[len(_norm(text)) :]
                        continue
                    consumed_suffix = ""
                if use_toc:
                    rest = self._match_toc(block, page_toc, builder, anchor)
                    if rest is not None:
                        text, consumed_suffix = rest
                        if not text:
                            continue
                elif (level := _font_heading_level(block, body, levels)) is not None:
                    builder.heading(text, level, anchor)
                    continue
                self._emit_text(builder, block_lines, text, anchor)
            if number_lines:
                builder.paragraph(_join_lines([ln.text for ln in number_lines]), anchor)
            # Outline entries whose line was not found start at the page top.
            for entry in page_toc:
                if not entry.done:
                    entry.done = True
                    before += 1
                    builder.nodes.insert(
                        before - 1,
                        HeadingNode(
                            node_id=f"t{pno:04d}{entry.norm[:8]}",
                            order=before - 1,
                            text=entry.title,
                            heading_path=builder.path(),
                            anchor=anchor,
                            level=min(max(entry.level, 1), 6),
                        ),
                    )
            for table in page_tables:  # tables with no text block over them
                builder.table(table[1], table[2], table[3], anchor)
            if len(builder.nodes) == page_start:
                empty_pages += 1

        if image_pages:
            warnings.append(f"{image_pages} page(s) contain images not represented in the tree")
        if empty_pages:
            warnings.append(f"{empty_pages} page(s) contributed no content")

        nodes = self._renumber(builder.nodes)
        full_text = "\n".join(n.text for n in nodes)
        from app.services.ingestion.administrative_extractor import AdministrativeMetadataExtractor

        admin_meta = AdministrativeMetadataExtractor.extract(full_text, filename=source.filename)
        return ParsedDocument(
            metadata=ParsedDocumentMetadata(
                source_id=source.source_id,
                filename=source.filename,
                parser_name=PYMUPDF_PARSER_NAME,
                parser_version=pymupdf_version(),
                page_count=doc.page_count,
                administrative_metadata=admin_meta.to_dict(),
                parse_warnings=tuple(warnings),
            ),
            nodes=tuple(nodes),
        )

    @staticmethod
    def _tables_of(page: Any) -> list[tuple[tuple[float, ...], str, int, int]]:
        found: list[tuple[tuple[float, ...], str, int, int]] = []
        try:
            for table in page.find_tables().tables:
                if table.row_count < 2 or table.col_count < 2:
                    continue
                markdown = (table.to_markdown() or "").strip()
                if markdown and not _LEADER.search(markdown):
                    found.append((tuple(table.bbox), markdown, table.row_count, table.col_count))
        except Exception:  # noqa: BLE001 - a table miss must not fail the page
            return []
        return found

    @staticmethod
    def _match_toc(
        block: _Block, page_toc: list[_TocEntry], builder: _Builder, anchor: SourceAnchor
    ) -> tuple[str, str] | None:
        """Emit an outline heading starting at this block.

        Returns (remaining body text, unmatched title suffix) when the block
        starts a section, None when it does not.
        """
        whole = _norm_unnumbered(block.text)
        if not whole:
            return None
        for entry in page_toc:
            if entry.done or not entry.norm:
                continue
            title_part = whole.startswith(entry.norm[:60])
            partial = len(whole) >= 6 and entry.norm.startswith(whole)
            if not (title_part or partial):
                continue
            entry.done = True
            if partial:
                # The block is the first part of a title split over blocks.
                builder.heading(_merge_number(block.text, entry.title), entry.level, anchor)
                return "", entry.norm[len(whole) :]
            # The block holds the title and maybe the first body lines.
            taken, consumed = 0, ""
            for line in block.lines:
                consumed += _norm_unnumbered(line.text) if not consumed else _norm(line.text)
                taken += 1
                if len(consumed) >= len(entry.norm):
                    break
            title_lines = " ".join(ln.text for ln in block.lines[:taken])
            heading_text = _merge_number(title_lines, entry.title)
            builder.heading(heading_text, entry.level, anchor)
            return _join_lines([ln.text for ln in block.lines[taken:]]), ""
        return None

    @staticmethod
    def _emit_text(builder: _Builder, lines: list[_Line], text: str, anchor: SourceAnchor) -> None:
        bullets = [bool(_BULLET.match(ln.text)) for ln in lines]
        if bullets and bullets[0] and sum(bullets) >= max(1, len(lines) // 2):
            items: list[list[str]] = []
            for line, is_bullet in zip(lines, bullets, strict=True):
                if is_bullet or not items:
                    items.append([_BULLET.sub("", line.text, count=1)])
                else:
                    items[-1].append(line.text)
            builder.list_items([_join_lines(i) for i in items if _join_lines(i)], anchor)
            return
        builder.paragraph(text, anchor)

    @staticmethod
    def _renumber(
        nodes: list[HeadingNode | ListNode | ParagraphNode | TableNode],
    ) -> list[HeadingNode | ListNode | ParagraphNode | TableNode]:
        """Stable ids/order after page-top heading insertion; fix heading paths."""
        out: list[HeadingNode | ListNode | ParagraphNode | TableNode] = []
        stack: list[tuple[int, str]] = []
        for index, node in enumerate(nodes):
            path = tuple(t for _, t in stack)
            out.append(
                node.model_copy(
                    update={"node_id": f"n{index:04d}", "order": index, "heading_path": path}
                )
            )
            if isinstance(node, HeadingNode):
                while stack and stack[-1][0] >= node.level:
                    stack.pop()
                stack.append((node.level, node.text))
        return out


def _font_heading_level(block: _Block, body: float, levels: dict[float, int]) -> int | None:
    """Heading level of a short block set in a larger (or bold body) font."""
    text = block.text
    if not text or len(text) > 150 or len(block.lines) > 3 or text.endswith((".", ",", ";")):
        return None
    near = [lvl for size, lvl in levels.items() if abs(size - block.size) <= 0.3]
    if near:
        return min(near)
    if block.bold and body > 0 and block.size >= body - 0.3 and len(text) <= 90:
        return max(levels.values(), default=1) + 1 if levels else 2
    return None


def _merge_number(block_text: str, title: str) -> str:
    """'1.2.1' + 'Getting Access…' → '1.2.1 Getting Access…' (keep the PDF's numbering)."""
    number = _LEADING_NUMBER.match(block_text.strip())
    prefix = number.group(0).strip() if number and number.group(0).strip() else ""
    bare = prefix.rstrip(".:)")
    if (
        bare
        and not title.lower().startswith(bare.lower())
        and not re.search(rf"(?<![\w.]){re.escape(bare)}(?![\w.])", title, re.IGNORECASE)
    ):
        return f"{prefix} {title}"
    return title
