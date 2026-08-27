> Active phase file for P9C. Read `../MASTER_PLAN.md` and the P9 overview first.
> Do not implement until the user sends `APPROVED P9C`.

# P9C — CHUNKING ENGINE — EXECUTION SPECIFICATION

## Objective

Implement the canonical V1 chunking strategy: structure-aware hierarchical
parent-child chunking with dedicated table semantics, deterministic IDs, and
explicit raw_text vs embedding_text separation. Pure functions over
`ParsedDocument`; no I/O, no embedding calls.

## Entry criteria

```text
[ ] P9B APPROVED (ParsedDocument tree + quality checks exist)
```

---

## P9C-1 — Strategy Protocols (was P9-09 core)

```python
class ParentChunkingStrategy(Protocol):
    def build_parents(
        self,
        document: ParsedDocument,
    ) -> list[ParentChunkDraft]: ...

class ChildChunkingStrategy(Protocol):
    def build_children(
        self,
        parent: ParentChunkDraft,
    ) -> list[ChildChunkDraft]: ...
```

### 9.1 Parent purpose

PARENT chunks are semantic context units for expansion/generation. They should
normally correspond to section / subsection / logical section group and MUST
respect heading/document boundaries.

Starting benchmark configuration:

```text
parent target: ~1200-2000 tokens
parent hard max: configurable
```

Starting values, not fixed constants. Smaller-than-target natural sections stay
intact. Oversized sections split at subsection -> paragraph groups ->
sentence/token fallback only if necessary. Never mix unrelated top-level
sections to reach target size.

### 9.2 Child purpose

CHILD chunks are precise retrieval units.

```text
child target: ~350-650 tokens
child hard max: configurable
```

Children inherit:

```text
document_id, document_version_id, parent_id,
heading_path, source/page anchors
```

Build children within a single PARENT. Boundary priority:

```text
paragraph/list boundaries > sentence boundaries > token-size fallback
```

A child MUST NOT cross semantic parent boundaries.

### 9.3 Search/index rule

Default V1: EMBED + INDEX CHILD; PARENT retained for context expansion.
Parent embeddings are optional and NOT added in V1 unless P10 benchmarks prove
they help.

### 9.4 Overlap rule

No blind global fixed overlap. Semantic boundaries first; small overlap only
where continuity requires it, tracked explicitly to aid deduplication.

### 9.5 Small-node merging

Very small adjacent paragraphs/list items may merge within the same semantic
parent/heading. Never merge across unrelated headings merely to reach size.

---

## P9C-2 — Hierarchy representation (was P9-10)

In-memory drafts must carry every field persistence will need:

```text
id, document_id, document_version_id,
level          # PARENT | CHILD | TABLE_CHILD
parent_id, heading_path, chunk_index,
raw_text, embedding_text, page_start, page_end,
token_count, content_hash
```

Parent/child relation MUST be queryable without reparsing the source file.
If existing tables lack any field, note it for the P9A-style migration check;
do not silently widen types here.

## P9C-3 — Parent identity (was P9-11)

Deterministic within a document version. Ingredients:

```text
document_version_id, heading_path, source block range,
parent ordinal, parent chunker version
```

Random UUIDs alone are insufficient if they prevent reproducibility/diffing.

## P9C-4 — Child identity (was P9-12)

Deterministic. Ingredients:

```text
parent_id, source block range, child ordinal,
child chunker version, content hash
```

A child references exactly one parent in V1.

## P9C-5 — raw_text vs embedding_text (was P9-13)

Store both explicitly. Example:

```text
raw_text:
"Hybrid retrieval combines dense and sparse retrieval..."

embedding_text:
"Section: RAG Architecture > Retrieval > Hybrid Retrieval

Hybrid retrieval combines dense and sparse retrieval..."
```

Rules:

- `raw_text` is citation/source truth;
- `embedding_text` may include lightweight heading/context enrichment;
- enrichment MUST NOT introduce claims not present in the source;
- no generative LLM rewriting of routine source text in V1.

## P9C-6 — Table strategy (was P9-14)

Small table: one retrievable TABLE_CHILD when it fits budget. Preserve
`table_id`, `heading_path`, `page`, row/column metadata where available.

Large table: chunk by logical row groups; each child repeats column headers:

```text
TABLE_CHILD 1: header + rows 1-20
TABLE_CHILD 2: header + rows 21-40
```

All table children link to the surrounding semantic parent. Never split a table
arbitrarily by character count.

## P9C-7 — Metadata contracts (was P9-15/16)

Parent minimum:

```text
parent_id, document_id, document_version_id, source_id, title,
heading_path, page_start/page_end when available, source block IDs,
raw_text, token_count, content_hash, parent_chunker_version
```

Child minimum:

```text
child_id, parent_id, document_id, document_version_id, source_id,
title, filename, mime_type, source_type,
page_start/page_end when available, heading_path, chunk_index,
source block IDs, raw_text, embedding_text, token_count, content_hash,
child_chunker_version, embedding_model/version
```

---

## Required tests (unit)

- parent boundary preservation;
- child never crosses parent;
- deterministic parent IDs;
- deterministic child IDs;
- small-node merge;
- long-section splitting ladder;
- table row-group chunking + header repetition;
- heading inheritance into children;
- raw vs embedding text separation;
- overlap tracking where used;
- normalization rules respected (whitespace/artifact cleanup allowed; flattening forbidden).

Normalization rules (from was-P9-08) apply inside this sub-phase's cleaning step:

```text
Allowed : whitespace normalize, parser-artifact removal, paragraph/list cleanup,
          heading + table preservation
Forbidden: flatten-to-one-string, remove headings pre-chunking,
           drop source-location metadata, generative LLM rewriting
```

## Definition of Done

```text
[ ] parent/child/table strategies implemented behind Protocols
[ ] deterministic IDs implemented
[ ] metadata contracts enforced by typed models
[ ] all unit tests green; full suite green (ruff + pyright + pytest)
[ ] Review Pack section generated (sample chunks from fixtures)
[ ] user APPROVED P9D next
```
