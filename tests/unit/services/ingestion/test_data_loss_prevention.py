from __future__ import annotations

import uuid

import pytest

from app.domain.models.retrieval import ExpansionPolicy, RetrievalQuery, RetrievedChunk
from app.infrastructure.db.models import _sanitize_document_metadata
from app.services.ingestion.parsing.markdown_parser import MarkdownDocumentParser
from app.services.ingestion.source import SourceDocument
from app.services.retrieval.expansion import ExpansionService


class FakeProvider:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    async def fetch(self, sql: str) -> list[dict]:
        del sql
        return self.rows


@pytest.mark.asyncio
async def test_markdown_parser_flushes_unclosed_fence_at_eof() -> None:
    source = SourceDocument(
        source_id="test-unclosed-fence",
        source_type="preparsed_markdown",
        filename="test.md",
    )
    content = b"""# Header 1

Here is some text.

```python
def foo():
    return 42
"""
    parser = MarkdownDocumentParser()
    parsed = await parser.parse(source, content)
    assert parsed is not None
    # Verify code block was not silently dropped
    para_nodes = [node for node in parsed.nodes if "def foo():" in node.text]
    assert len(para_nodes) == 1
    assert "return 42" in para_nodes[0].text


def test_sanitize_document_metadata_permits_large_metadata() -> None:
    # Build metadata with 250 items and long values (> 2000 chars)
    payload = {f"key_{i}": f"value_{i}_" + "x" * 100 for i in range(150)}
    sanitized = _sanitize_document_metadata(payload)
    assert isinstance(sanitized, dict)
    assert len(sanitized) == 150
    # Values should not be truncated with "... [TRUNCATED]"
    assert "... [TRUNCATED]" not in str(sanitized["key_0"])


@pytest.mark.asyncio
async def test_orphan_chunk_preserved_during_parent_expansion() -> None:
    fake_provider = FakeProvider(rows=[])
    service = ExpansionService(fake_provider)

    orphan_chunk = RetrievedChunk(
        chunk_id=uuid.uuid4().hex,
        parent_id=None,  # Orphan: has no parent
        document_id=uuid.uuid4().hex,
        content_raw="Orphan child content that should not be dropped",
        score=0.95,
        rerank_score=0.95,
        retrieval_type="dense",
        metadata={"document_title": "Doc 1"},
    )
    query = RetrievalQuery(
        original_query="test question",
        search_query="test question",
        requester_id="user-1",
    )

    units = await service.build_units([orphan_chunk], ExpansionPolicy.PARENT, query)
    assert len(units) == 1
    assert units[0].primary_chunk_id == orphan_chunk.chunk_id
    assert units[0].kind == "CHILD"
    assert units[0].content_raw == "Orphan child content that should not be dropped"


@pytest.mark.asyncio
async def test_orphan_chunk_preserved_during_neighbor_expansion() -> None:
    fake_provider = FakeProvider(rows=[])
    service = ExpansionService(fake_provider)

    orphan_chunk = RetrievedChunk(
        chunk_id=uuid.uuid4().hex,
        parent_id=None,  # Orphan: has no parent
        document_id=uuid.uuid4().hex,
        content_raw="Orphan child content for neighbor expansion",
        score=0.92,
        rerank_score=0.92,
        retrieval_type="dense",
        metadata={"document_title": "Doc 1"},
    )
    query = RetrievalQuery(
        original_query="test question",
        search_query="test question",
        requester_id="user-1",
    )

    units = await service.build_units([orphan_chunk], ExpansionPolicy.NEIGHBORS, query)
    assert len(units) == 1
    assert units[0].primary_chunk_id == orphan_chunk.chunk_id
    assert units[0].kind == "CHILD"
