"""Images are kept in reading order, stored once and described at ingestion."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.core.images import IMAGE_REF, image_file
from app.domain.models.ingestion.parsed_document import (
    ParagraphNode,
    ParsedDocument,
    ParsedDocumentMetadata,
)
from app.services.ingestion.enrichment import images as module
from app.services.ingestion.enrichment.images import ImageDescriber, store_images
from app.services.ingestion.parsing.images import ImageCollector


def _png(width: int, height: int, value: int = 200) -> bytes:
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, (value, value, value))
    return pix.tobytes("png")


@pytest.fixture(autouse=True)
def image_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DOC_IMAGES_DIR", str(tmp_path))
    return tmp_path


def test_collector_numbers_images_and_skips_icons() -> None:
    collector = ImageCollector()
    dialog = _png(300, 200)
    first = collector.add(dialog, "png", 300, 200, page=2)
    assert collector.add(_png(20, 20), "png", 20, 20, page=2) is None  # an icon
    second = collector.add(dialog, "png", 300, 200, page=8)  # same dialog in a later step
    assert first and second and first.startswith("![Hình 1]") and second.startswith("![Hình 2]")
    assert len(collector.images) == 1  # stored once


def _document(collector: ImageCollector, *texts: str) -> ParsedDocument:
    return ParsedDocument(
        metadata=ParsedDocumentMetadata(
            source_id="s", filename="huong-dan.pdf", parser_name="docling", parser_version="1"
        ),
        nodes=tuple(ParagraphNode(node_id=f"n{i}", order=i, text=t) for i, t in enumerate(texts)),
        images=collector.images,
    )


def test_images_are_stored_and_described_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_describe(system: str, text: str, image: bytes, media_type: str, **_: Any) -> str:
        calls.append(text)
        return "Hộp thoại License Agreement, cần bấm Accept."

    monkeypatch.setattr(module, "llm_describe_image", fake_describe)
    collector = ImageCollector()
    ref = collector.add(_png(300, 200), "png", 300, 200, page=2) or ""
    again = collector.add(_png(300, 200), "png", 300, 200, page=8) or ""
    doc = _document(collector, "Tiếp tục chọn Accept.", ref, "Bước sau.", again)
    store_images(doc)
    key = doc.images[0].key
    assert image_file(key) is not None

    out = asyncio.run(ImageDescriber().adescribe(doc))
    assert len(calls) == 1 and "Tiếp tục chọn Accept." in calls[0]
    alts = [m.group("alt") for n in out.nodes for m in IMAGE_REF.finditer(n.text)]
    assert alts == [
        "Hình 1 — mô tả tự động: Hộp thoại License Agreement, cần bấm Accept.",
        "Hình 2 — mô tả tự động: Hộp thoại License Agreement, cần bấm Accept.",
    ]
    # A re-ingest reuses the cached description.
    asyncio.run(ImageDescriber().adescribe(doc))
    assert len(calls) == 1


def test_failed_description_keeps_the_plain_label(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(module, "llm_describe_image", fail)
    collector = ImageCollector()
    ref = collector.add(_png(300, 200, 90), "png", 300, 200, page=1) or ""
    out = asyncio.run(ImageDescriber().adescribe(_document(collector, ref)))
    assert out.nodes[0].text == ref


def test_pymupdf_parser_keeps_images_in_reading_order() -> None:
    import pymupdf

    from app.domain.models.ingestion.documents import SourceDocument
    from app.services.ingestion.parsing.pymupdf_parser import PyMuPDFDocumentParser

    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 80), "Step 1: open Dism++ and press Accept.", fontname="helv")
    page.insert_image(pymupdf.Rect(72, 100, 372, 300), stream=_png(300, 200))
    page.insert_text((72, 340), "Step 2: open Toolkit.", fontname="helv")
    content = pdf.tobytes()
    source = SourceDocument(
        source_id="s", filename="guide.pdf", source_type="upload", mime_type="application/pdf"
    )
    parsed = asyncio.run(PyMuPDFDocumentParser().parse(source, content))
    texts = [n.text for n in parsed.nodes]
    image_at = next(i for i, t in enumerate(texts) if IMAGE_REF.fullmatch(t))
    assert "Step 1" in " ".join(texts[:image_at]) and "Step 2" in " ".join(texts[image_at:])
    assert len(parsed.images) == 1


def test_images_take_the_source_figure_label() -> None:
    from app.services.ingestion.enrichment.images import adopt_captions

    collector = ImageCollector()
    ref = collector.add(_png(300, 200), "png", 300, 200, page=40) or ""
    doc = _document(
        collector, "Kiến trúc gồm ba tầng.", ref, "Hình 4.3: Kiến trúc tổng thể của hệ thống"
    )
    out = adopt_captions(doc)
    alt = IMAGE_REF.fullmatch(out.nodes[1].text).group("alt")
    assert alt == "Hình 4.3: Kiến trúc tổng thể của hệ thống"
    assert out.nodes[2].text.startswith("Hình 4.3")  # the caption text stays
