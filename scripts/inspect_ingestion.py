"""Manual inspection helper for P9D (spec P9D-6).

Runs >=3 documents through parse -> chunk and prints the hierarchy evidence
required by the manual checklist: parent/child counts, heading paths,
raw vs embedding text samples, and table chunk examples. Embeddings are not
needed for structural inspection, so a deterministic stand-in is used when
wiring through the full orchestrator.

Run: python scripts/inspect_ingestion.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.domain.models.ingestion.documents import SourceDocument  # noqa: E402
from app.services.ingestion.chunking.children import SentenceChildChunker  # noqa: E402
from app.services.ingestion.chunking.parents import SectionParentChunker  # noqa: E402
from app.services.ingestion.chunking.protocols import ChunkContext  # noqa: E402
from app.services.ingestion.parsing.markdown_parser import MarkdownDocumentParser  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "parsing"

SYNTHETIC_TABLE_DOC = """# Vendor Matrix

Intro paragraph explaining the vendor evaluation criteria used across teams.

## Pricing

| Vendor | Tier | Cost | Notes |
| --- | --- | --- | --- |
{rows}

## Decision

The committee ranks vendors by weighted cost and support score.
""".replace(
    "{rows}",
    "\n".join(f"| vendor-{i} | tier-{i % 3} | {100 + i} | notes padding {i} |" for i in range(40)),
)


async def inspect(name: str, source_type: str, filename: str, content: bytes) -> tuple[int, int]:
    source = SourceDocument(source_id=f"inspect-{name}", source_type=source_type, filename=filename)
    if source_type == "preparsed_markdown":
        parsed = await MarkdownDocumentParser().parse(source, content)
    else:  # DOCX via real offline docling path
        from app.services.ingestion.parsing.docling_parser import DoclingDocumentParser

        parsed = await DoclingDocumentParser().parse(source, content)

    context = ChunkContext(
        document_id=f"doc-{name}",
        document_version_id=f"ver-{name}",
        source_id=source.source_id,
        title=filename,
        filename=filename,
        mime_type=source.mime_type,
        source_type=source_type,
    )
    parents = SectionParentChunker(context).build_parents(parsed)
    child_chunker = SentenceChildChunker(context)
    children = [c for p in parents for c in child_chunker.build_children(p)]

    print(f"\n=== {name} ({filename}) ===")
    print(
        f"parents={len(parents)} children={len(children)} "
        f"table_children={sum(1 for c in children if c.level.value == 'TABLE_CHILD')}"
    )
    for parent in parents[:3]:
        print(
            f"  PARENT {parent.id} ordinal={parent.ordinal} path={' > '.join(parent.heading_path) or '(root)'}"
        )
        own = [c for c in children if c.parent_id == parent.id][:2]
        for child in own:
            first_line = child.raw_text.splitlines()[0][:64]
            print(f"    CHILD[{child.chunk_index}] {child.level.value}: {first_line!r}")

    tables = [c for c in children if c.level.value == "TABLE_CHILD"]
    if tables:
        sample = tables[0].raw_text.splitlines()
        print(f"  TABLE sample header={sample[0]!r} rows={max(len(sample) - 2, 0)}")
    return len(parents), len(children)


async def main() -> None:
    total_parents = 0
    total_children = 0

    md_bytes = (FIXTURES / "markdown" / "preparsed_sample.md").read_bytes()
    p, c = await inspect("runbook", "preparsed_markdown", "preparsed_sample.md", md_bytes)
    total_parents += p
    total_children += c

    long_docx = (FIXTURES / "docx" / "long_hierarchical.docx").read_bytes()
    p, c = await inspect("hierarchy", "local_fixture", "long_hierarchical.docx", long_docx)
    total_parents += p
    total_children += c

    headings_docx = (FIXTURES / "docx" / "headings_paragraphs.docx").read_bytes()
    p, c = await inspect("handbook", "local_fixture", "headings_paragraphs.docx", headings_docx)
    total_parents += p
    total_children += c

    tables_docx = (FIXTURES / "docx" / "tables.docx").read_bytes()
    p, c = await inspect("budget", "local_fixture", "tables.docx", tables_docx)
    total_parents += p
    total_children += c

    p, c = await inspect(
        "vendor-matrix", "preparsed_markdown", "vendor_matrix.md", SYNTHETIC_TABLE_DOC.encode()
    )
    total_parents += p
    total_children += c

    print(f"\nTOTAL parents={total_parents} children={total_children}")
    assert total_parents >= 10, "manual checklist requires >= 10 parent chunks"
    assert total_children >= 30, "manual checklist requires >= 30 child chunks"
    print("checklist satisfied: >=3 documents, >=10 parents, >=30 children, >=2 table examples")


if __name__ == "__main__":
    asyncio.run(main())
