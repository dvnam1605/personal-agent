> Active phase file for P9B. Read `../MASTER_PLAN.md` and the P9 overview first.
> Do not implement until the user sends `APPROVED P9B`.

# P9B — PARSING LAYER — EXECUTION SPECIFICATION

## Objective

Turn raw bytes (PDF/DOCX/Markdown) into a provider-independent normalized
document tree, with parse-quality gating and a frozen OCR engine decision.
Docling becomes a real dependency here; nothing above this layer sees
provider types.

## Entry criteria

```text
[ ] P9A APPROVED (SourceDocument + fingerprint + detection exist)
[ ] docling dependency added to pyproject.toml
[ ] fixture corpus available under tests fixtures
```

---

## P9B-1 — Parser adapter (was P9-04)

Docling hidden behind a Protocol:

```python
class DocumentParser(Protocol):
    async def parse(
        self,
        source: SourceDocument,
        content: bytes,
    ) -> ParsedDocument: ...
```

Implement:

```text
DoclingDocumentParser   (born-digital PDF + DOCX)
MarkdownDocumentParser  (preparsed_markdown sources; headings/tables from MD)
```

Docling-specific objects MUST NOT leak into retrieval, agents, or API contracts.

## P9B-2 — Canonical normalized document tree (was P9-05)

Provider-independent structures:

```text
ParsedDocument
├── metadata
└── nodes[]
    ├── HeadingNode
    ├── ParagraphNode
    ├── ListNode
    ├── TableNode
    └── SourceAnchor/PageBoundary
```

Every useful node preserves:

```text
node_id, node_type, order, text, heading_path,
page number/range where available, source anchor/block metadata
```

DOCX page numbers may be unavailable or unstable; section/heading provenance is
required there.

## P9B-3 — Parse quality checks (was P9-06)

Quality report before chunking:

```text
text_length, node_count, heading_count, table_count,
empty_page_count, ocr_used, parse_warnings
```

Flag or reject obviously broken output:

```text
large source + zero useful text
empty document tree
extreme parser garbage
```

Do not embed corrupt output. Broken parses produce a typed failure status so a
batch continues with other files.

## P9B-4 — OCR strategy decision (was P9-07)

OCR is conditional fallback, not default for every PDF.

Flow:

```text
normal parse -> useful text sufficient? -> yes: continue / no: OCR-capable fallback
```

Record `ocr_used`, `ocr_pages` if available, warnings. Behavior configurable.

### Engine decision (ADR required in this sub-phase)

```text
primary parser      : Docling (born-digital PDF + DOCX, no OCR)
ocr fallback engine : PaddleOCR-VL-1.6  (pin paddleocr >=3.7,<4)
local backend       : llama-cpp-server with GGUF Q4_K_M (GTX 1650 4GB, page-by-page)
strong-GPU path     : same engine via the offline batch script (P9E)
config fallback     : PP-StructureV3 for CPU-only machines
runtime V1 default  : scanned PDFs get typed status NEEDS_OCR (queued for the
                      offline path); inline OCR fallback is config-gated OFF
```

The ADR lands as an update to
`docs/architecture/document-ingestion-strategy.md`. The engine sits behind the
same `DocumentParser` Protocol. The assistant runtime never requires the OCR
model installed.

## P9B-5 — Fixture corpus (was P9-22, parsing part)

Required fixtures:

```text
PDF:
- normal text
- multi-column
- table-heavy
- long sections
- scanned/OCR case if feasible
- pre-parsed Markdown sample (P9E output shape)

DOCX:
- headings + paragraphs
- lists
- tables
- long hierarchical document

Failure:
- corrupt PDF
- unsupported input
```

---

## Required tests

- PDF fixture -> normalized tree (headings, paragraphs, tables, anchors);
- DOCX same;
- Markdown parser -> equivalent tree;
- quality checks reject corrupt/degenerate parses with typed statuses;
- no Docling type leaks outside the parser module (import-linter or reflection test);
- unsupported/corrupt inputs isolated per file.

## Definition of Done

```text
[ ] DocumentParser Protocol + Docling/Markdown adapters implemented
[ ] normalized tree types implemented
[ ] quality report implemented
[ ] OCR engine ADR recorded; NEEDS_OCR typed status exists
[ ] fixture corpus committed
[ ] unit tests green; full suite green (ruff + pyright + pytest)
[ ] Review Pack section generated
[ ] user APPROVED P9C next
```
