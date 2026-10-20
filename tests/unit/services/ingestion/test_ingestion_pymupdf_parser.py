"""PyMuPDF parser: outline headings, running footers, lists, routing vs Docling."""

from __future__ import annotations

import pytest

pymupdf = pytest.importorskip("pymupdf")

from app.core.config import ParsingSettings  # noqa: E402
from app.domain.models.ingestion.documents import SourceDocument  # noqa: E402
from app.domain.models.ingestion.parsed_document import (  # noqa: E402
    HeadingNode,
    ListNode,
    ParagraphNode,
    ParseStatus,
)
from app.services.ingestion.parsing.base import (  # noqa: E402
    build_parser_for_type,
    parse_source,
    parser_identity,
)
from app.services.ingestion.parsing.pymupdf_parser import (  # noqa: E402
    PyMuPDFDocumentParser,
    prefer_pymupdf,
    vietnamese_glued_ratio,
)
from app.services.ingestion.type_detection import DetectedDocumentType  # noqa: E402

BODY = (
    "Multi-agent systems split a complex task across several cooperating agents. "
    "Each agent has a role, tools and memory, and an orchestrator decides who acts next. "
)


def _pdf(pages: int, *, toc: bool = True, text: str = BODY) -> bytes:
    doc = pymupdf.open()
    outline = []
    for i in range(pages):
        page = doc.new_page()
        y = 72
        if i % 2 == 0:
            title = f"Section {i // 2 + 1} Topic"
            page.insert_text((72, y), f"{i // 2 + 1}.1", fontsize=16)
            page.insert_text((72, y + 20), title.split(" ", 2)[2], fontsize=16)
            outline.append([1, title.split(" ", 2)[2], i + 1])
            y += 50
        for line in range(6):
            page.insert_text((72, y + line * 14), text[line * 20 : line * 20 + 60], fontsize=10)
        page.insert_text((72, 400), "• first point of the list", fontsize=10)
        page.insert_text((72, 414), "• second point of the list", fontsize=10)
        page.insert_text((250, 820), f"Designing Agents | page {i + 1}", fontsize=8)
    if toc:
        doc.set_toc(outline)
    return doc.tobytes()


def _source(name: str = "book.pdf") -> SourceDocument:
    return SourceDocument(
        source_id=f"test-{name}",
        filename=name,
        mime_type="application/pdf",
        source_type="upload",
        checksum="0" * 64,
        size_bytes=1,
    )


async def test_outline_headings_footer_removal_and_lists() -> None:
    parsed = await PyMuPDFDocumentParser(detect_tables=False).parse(_source(), _pdf(6))
    headings = [n for n in parsed.nodes if isinstance(n, HeadingNode)]
    assert [h.text for h in headings] == ["1.1 Topic", "2.1 Topic", "3.1 Topic"]
    assert [h.anchor.page_start for h in headings] == [1, 3, 5]
    texts = " ".join(n.text for n in parsed.nodes)
    assert "Designing Agents" not in texts  # running footer dropped
    lists = [n for n in parsed.nodes if isinstance(n, ListNode)]
    assert lists and lists[0].items[:2] == ("first point of the list", "second point of the list")
    paragraph = next(n for n in parsed.nodes if isinstance(n, ParagraphNode))
    assert paragraph.heading_path == ("1.1 Topic",)
    assert parsed.metadata.parser_name == "pymupdf"


async def test_font_size_headings_without_outline() -> None:
    parsed = await PyMuPDFDocumentParser(detect_tables=False).parse(_source(), _pdf(4, toc=False))
    headings = [n.text for n in parsed.nodes if isinstance(n, HeadingNode)]
    assert "1.1 Topic" in headings and "2.1 Topic" in headings


def test_routing_auto_uses_pymupdf_only_for_long_text_pdfs() -> None:
    long_pdf, short_pdf = _pdf(6), _pdf(2)
    assert prefer_pymupdf(long_pdf, engine="auto", min_pages=5)
    assert not prefer_pymupdf(short_pdf, engine="auto", min_pages=5)
    assert prefer_pymupdf(short_pdf, engine="pymupdf", min_pages=5)
    assert not prefer_pymupdf(long_pdf, engine="docling", min_pages=5)
    assert not prefer_pymupdf(b"not a pdf", engine="pymupdf", min_pages=1)


def test_image_only_pdf_stays_on_docling() -> None:
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    assert not prefer_pymupdf(doc.tobytes(), engine="pymupdf", min_pages=1)


def test_glued_vietnamese_text_layer_is_detected() -> None:
    clean = "hệ thống hỗ trợ tra cứu thông tin kỹ thuật cho người dùng " * 50
    glued = "hệthống hỗtrợ tra cứu thôngtin kỹthuật cho ngườidùng " * 50
    assert vietnamese_glued_ratio(clean) < 0.05
    assert vietnamese_glued_ratio(glued) > 0.05
    assert vietnamese_glued_ratio("short english text") == 0.0


def test_build_parser_and_identity_follow_settings() -> None:
    settings = ParsingSettings(pdf_engine="auto", pymupdf_min_pages=5)
    parser = build_parser_for_type(
        DetectedDocumentType.PDF, source_type="upload", content=_pdf(6), settings=settings
    )
    assert isinstance(parser, PyMuPDFDocumentParser)
    name, _version = parser_identity(_source(), _pdf(6), settings=settings)
    assert name == "pymupdf"
    name, _version = parser_identity(
        _source(), _pdf(6), settings=ParsingSettings(pdf_engine="docling")
    )
    assert name == "docling"


async def test_parse_source_routes_long_pdf_to_pymupdf() -> None:
    result = await parse_source(
        _source(), _pdf(6), settings=ParsingSettings(pdf_engine="auto", pymupdf_min_pages=5)
    )
    assert result.status is ParseStatus.PARSED
    assert result.document is not None
    assert result.document.metadata.parser_name == "pymupdf"
