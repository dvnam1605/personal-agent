"""Unit tests for MarkdownDocumentParser (spec P9B-1/P9B-5)."""

from __future__ import annotations

import pytest

from app.domain.models.ingestion.documents import SourceDocument
from app.domain.models.ingestion.parsed_document import NodeType
from app.services.ingestion.parsing.base import DocumentParseError
from app.services.ingestion.parsing.markdown_parser import (
    MARKDOWN_PARSER_NAME,
    MARKDOWN_PARSER_VERSION,
    MarkdownDocumentParser,
)
from tests.unit.services._repo import fixtures_root

FIXTURE = fixtures_root() / "parsing" / "markdown" / "preparsed_sample.md"


def _source(**overrides: object) -> SourceDocument:
    values: dict[str, object] = {
        "source_id": "md-1",
        "source_type": "preparsed_markdown",
        "filename": "preparsed_sample.md",
    }
    values.update(overrides)
    return SourceDocument(**values)  # type: ignore[arg-type]


async def test_fixture_parses_full_structure() -> None:
    parsed = await MarkdownDocumentParser().parse(_source(), FIXTURE.read_bytes())
    assert parsed.metadata.parser_name == MARKDOWN_PARSER_NAME
    assert parsed.metadata.parser_version == MARKDOWN_PARSER_VERSION

    headings = [node for node in parsed.nodes if node.node_type is NodeType.HEADING]
    titles = [node.text for node in headings]
    assert titles == ["Deployment Runbook", "Preconditions", "Rollout", "Rollback"]

    tables = [node for node in parsed.nodes if node.node_type is NodeType.TABLE]
    assert len(tables) == 1
    assert tables[0].row_count == 2
    assert tables[0].column_count == 3

    lists = [node for node in parsed.nodes if node.node_type is NodeType.LIST]
    assert len(lists) == 1
    assert lists[0].items == (
        "Announce the window in the status channel",
        "Apply migrations with the locked deploy account",
        "Watch error dashboards for ten minutes",
    )


async def test_heading_paths_follow_hierarchy() -> None:
    parsed = await MarkdownDocumentParser().parse(_source(), FIXTURE.read_bytes())
    by_title = {node.text: node for node in parsed.nodes}
    rollback = by_title["Rollback"]
    assert rollback.heading_path == ("Deployment Runbook", "Rollout")
    preconditions = by_title["Preconditions"]
    assert preconditions.heading_path == ("Deployment Runbook",)
    body_nodes = [
        node
        for node in parsed.nodes
        if node.node_type is NodeType.PARAGRAPH and "release tag" in node.text
    ]
    assert body_nodes[0].heading_path == ("Deployment Runbook", "Preconditions")


async def test_table_rows_keep_cells_intact() -> None:
    content = b"# T\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"
    parsed = await MarkdownDocumentParser().parse(_source(), content)
    tables = [node for node in parsed.nodes if node.node_type is NodeType.TABLE]
    assert tables[0].row_count == 2
    assert tables[0].column_count == 2
    assert "| 3 | 4 |" in tables[0].markdown


async def test_short_table_separator_still_detected() -> None:
    """Single-dash separators are valid GFM pipes (review L7)."""
    content = b"| A | B |\n| - | - |\n| 1 | 2 |\n"
    parsed = await MarkdownDocumentParser().parse(_source(), content)
    tables = [node for node in parsed.nodes if node.node_type is NodeType.TABLE]
    assert len(tables) == 1
    assert tables[0].row_count == 1
    assert tables[0].column_count == 2


async def test_numbered_lists_group_into_single_node() -> None:
    content = b"1. one\n2. two\n\nafter"
    parsed = await MarkdownDocumentParser().parse(_source(), content)
    lists = [node for node in parsed.nodes if node.node_type is NodeType.LIST]
    assert len(lists) == 1
    assert lists[0].items == ("one", "two")


async def test_fenced_code_block_preserved_verbatim() -> None:
    content = b"# T\n```bash\nmake release --flag=1\n```\n"
    parsed = await MarkdownDocumentParser().parse(_source(), content)
    paragraphs = [node for node in parsed.nodes if node.node_type is NodeType.PARAGRAPH]
    assert any("make release --flag=1" in node.text for node in paragraphs)


async def test_empty_markdown_produces_empty_tree() -> None:
    parsed = await MarkdownDocumentParser().parse(_source(filename="empty.md"), b"\n\n")
    assert parsed.nodes == ()


async def test_invalid_utf8_raises_document_parse_error() -> None:
    with pytest.raises(DocumentParseError):
        await MarkdownDocumentParser().parse(_source(), b"\xff\xfe\x00bad")
