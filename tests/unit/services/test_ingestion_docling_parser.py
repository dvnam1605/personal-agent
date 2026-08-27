"""Unit tests for DoclingDocumentParser with a mocked engine (spec P9B-1).

The engine is replaced by a fake converter so the walker/normalization logic
is verified deterministically, without model downloads. A reflection test
additionally proves docling never leaks above the parsing package.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from docling.datamodel.base_models import ConversionStatus
from docling_core.types.doc.base import BoundingBox
from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.table.table_data import TableData
from docling_core.types.doc.items.text import (
    ListItem,
    SectionHeaderItem,
    TextItem,
    TitleItem,
)
from docling_core.types.doc.labels import DocItemLabel

from app.domain.models.documents import SourceDocument
from app.domain.models.parsed_document import NodeType
from app.services.ingestion.parsing import base as parsing_base
from app.services.ingestion.parsing.docling_parser import (
    DOCLING_PARSER_NAME,
    DoclingDocumentParser,
)
from app.services.ingestion.parsing.markdown_parser import MarkdownDocumentParser

REPO_ROOT = Path(__file__).resolve().parents[3]


_REFS = iter(range(10000))


def _ref() -> str:
    return f"#/texts/{next(_REFS)}"


def _prov(page_no: int) -> list[ProvenanceItem]:
    return [
        ProvenanceItem(
            page_no=page_no,
            bbox=BoundingBox(l=0, t=0, r=10, b=10),
            charspan=(0, 4),
        )
    ]


def _title(text: str) -> TitleItem:
    return TitleItem(self_ref=_ref(), orig=text, text=text)


def _header(text: str, level: int, page: int = 1) -> SectionHeaderItem:
    return SectionHeaderItem(self_ref=_ref(), orig=text, text=text, level=level, prov=_prov(page))


def _paragraph(text: str, page: int = 1) -> TextItem:
    return TextItem(
        self_ref=_ref(), orig=text, text=text, label=DocItemLabel.TEXT, prov=_prov(page)
    )


def _list_item(text: str) -> ListItem:
    return ListItem(self_ref=_ref(), orig=text, text=text, marker="-")


class _FakeTableItem(TableItem):
    def export_to_markdown(self, document: object | None = None) -> str:
        return "| a | b |\n| --- | --- |\n| 1 | 2 |"


def _table() -> TableItem:
    item = _FakeTableItem(
        self_ref="#/tables/9", data=TableData(num_rows=1, num_cols=2, table_cells=[])
    )
    item.prov = _prov(2)
    return item


class FakeDocument:
    def __init__(self, items: list[object], pages: dict[int, object] | None = None):
        self._items = [(item, 0) for item in items]
        self.pages = pages if pages is not None else {}

    def iterate_items(self) -> list[tuple[object, int]]:
        return self._items


class FakeConverter:
    def __init__(self, document: FakeDocument, status: ConversionStatus = ConversionStatus.SUCCESS):
        self.document = document
        self.status = status
        self.errors: list[str] = []

    def convert(self, stream: object, raises_on_error: bool = True) -> SimpleNamespace:
        return SimpleNamespace(document=self.document, status=self.status, errors=self.errors)


class ExplodingConverter(FakeConverter):
    def convert(self, stream: object, raises_on_error: bool = True) -> SimpleNamespace:
        raise RuntimeError("pdfium exploded")


@pytest.fixture()
def source() -> SourceDocument:
    return SourceDocument(source_id="pdf-1", source_type="upload", filename="doc.pdf")


async def test_full_tree_mapping_with_headings_and_paths(
    monkeypatch: pytest.MonkeyPatch, source: SourceDocument
) -> None:
    document = FakeDocument(
        [
            _title("Annual Report"),
            _header("Finance", 1),
            _paragraph("Revenue details for the year.", page=1),
            _header("Outlook", 2, page=2),
            _list_item("first"),
            _list_item("second"),
            _table(),
        ]
    )
    monkeypatch.setattr(
        "app.services.ingestion.parsing.docling_parser._converter", FakeConverter(document)
    )

    parsed = await DoclingDocumentParser().parse(source, b"%PDF-fake")

    assert parsed.metadata.parser_name == DOCLING_PARSER_NAME
    kinds = [node.node_type for node in parsed.nodes]
    assert kinds == ["heading", "heading", "paragraph", "heading", "list", "table"]
    by_text = {node.text: node for node in parsed.nodes}
    finance = by_text["Finance"]
    assert isinstance(finance.anchor, object)
    outlook = by_text["Outlook"]
    assert outlook.heading_path == ("Annual Report", "Finance")
    revenue = by_text["Revenue details for the year."]
    assert revenue.heading_path == ("Annual Report", "Finance")
    table_node = by_text["| a | b |\n| --- | --- |\n| 1 | 2 |"]
    assert table_node.node_type is NodeType.TABLE
    assert table_node.row_count == 1 and table_node.column_count == 2


async def test_list_items_merge_into_single_node(
    monkeypatch: pytest.MonkeyPatch, source: SourceDocument
) -> None:
    document = FakeDocument([_list_item("one"), _list_item("two"), _list_item("three")])
    monkeypatch.setattr(
        "app.services.ingestion.parsing.docling_parser._converter", FakeConverter(document)
    )
    parsed = await DoclingDocumentParser().parse(source, b"pdf")
    lists = [node for node in parsed.nodes if node.node_type is NodeType.LIST]
    assert len(lists) == 1
    assert lists[0].items == ("one", "two", "three")


async def test_page_anchors_and_empty_page_warning(
    monkeypatch: pytest.MonkeyPatch, source: SourceDocument
) -> None:
    document = FakeDocument(
        [_paragraph("Only page one content.")], pages={1: object(), 2: object()}
    )
    monkeypatch.setattr(
        "app.services.ingestion.parsing.docling_parser._converter", FakeConverter(document)
    )
    parsed = await DoclingDocumentParser().parse(source, b"pdf")
    anchor = parsed.nodes[0].anchor
    assert anchor is not None
    assert anchor.page_start == 1
    assert any(
        "1 page(s) contributed no content" in warning for warning in parsed.metadata.parse_warnings
    )


async def test_engine_failure_raises_document_parse_error(
    monkeypatch: pytest.MonkeyPatch, source: SourceDocument
) -> None:
    monkeypatch.setattr(
        "app.services.ingestion.parsing.docling_parser._converter",
        ExplodingConverter(FakeDocument([])),
    )
    with pytest.raises(parsing_base.DocumentParseError):
        await DoclingDocumentParser().parse(source, b"pdf")


async def test_partial_success_appends_warnings(
    monkeypatch: pytest.MonkeyPatch, source: SourceDocument
) -> None:
    converter = FakeConverter(
        FakeDocument([_paragraph("partial body text")]), ConversionStatus.PARTIAL_SUCCESS
    )
    converter.errors = ["page 9 failed to render"]
    monkeypatch.setattr("app.services.ingestion.parsing.docling_parser._converter", converter)
    parsed = await DoclingDocumentParser().parse(source, b"pdf")
    assert "page 9 failed to render" in parsed.metadata.parse_warnings


async def test_pictures_are_skipped_with_warning(
    monkeypatch: pytest.MonkeyPatch, source: SourceDocument
) -> None:
    picture = PictureItem(self_ref="#/pictures/1")
    document = FakeDocument([picture, _paragraph("real content here")])
    monkeypatch.setattr(
        "app.services.ingestion.parsing.docling_parser._converter", FakeConverter(document)
    )
    parsed = await DoclingDocumentParser().parse(source, b"pdf")
    assert [node.node_type for node in parsed.nodes] == [NodeType.PARAGRAPH]
    assert "pictures are not represented in the V1 tree" in parsed.metadata.parse_warnings


async def test_docx_fixture_parses_offline_end_to_end() -> None:
    fixture = REPO_ROOT / "tests" / "fixtures" / "parsing" / "docx" / "headings_paragraphs.docx"
    source = SourceDocument(
        source_id="docx-1", source_type="local_fixture", filename="headings_paragraphs.docx"
    )
    parsed = await DoclingDocumentParser().parse(source, fixture.read_bytes())
    headings = [node for node in parsed.nodes if node.node_type is NodeType.HEADING]
    assert [node.text for node in headings][:2] == ["Project Handbook", "Onboarding"]
    paragraphs = [node for node in parsed.nodes if node.node_type is NodeType.PARAGRAPH]
    assert any("mentor" in node.text for node in paragraphs)


def test_docling_never_leaks_above_the_parsing_package() -> None:
    """Importing the public ingestion surface must not pull in docling."""
    code = (
        "import sys; import app.services.ingestion; "
        "leaked = [m for m in sys.modules if m.split('.')[0] == 'docling']; "
        "assert not leaked, leaked"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_build_parser_for_type_routes_preparsed_markdown_to_md_parser() -> None:
    parser = parsing_base.build_parser_for_type(None, source_type="preparsed_markdown")
    assert isinstance(parser, MarkdownDocumentParser)
