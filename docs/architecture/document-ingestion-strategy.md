# Document Ingestion Strategy (V1)

**Status:** APPROVED (P9A-1); OCR engine decision pinned in P9B (see Engine decisions)
**Supersedes:** nothing
**Related:** `docs/adr/0012-local-vietnamese-embedding-and-reranker-models.md`,
`plan/phases/P09_document_ingestion_pipeline_execution_specification.md`

## Canonical strategy

> **Structure-aware hierarchical parent–child chunking.**

Document structure decides natural boundaries; PARENT chunks preserve wider
semantic context for generation-time expansion; CHILD chunks preserve retrieval
precision and are the default embedded/indexed units; tables use a dedicated
row-group strategy; raw source text and provenance are preserved separately
from embedding text.

Final retrieval/answer behavior belongs to P10.

## Canonical flow

```text
SOURCE DISCOVERY -> SOURCE SNAPSHOT/FINGERPRINT -> TYPE DETECTION
-> PARSE WITH DOCLING -> NORMALIZE DOCUMENT TREE -> PARSE QUALITY CHECK
-> BUILD SEMANTIC PARENT NODES -> BUILD RETRIEVAL CHILD NODES
-> TABLE-SPECIFIC CHUNKING -> CHUNK ENRICHMENT + PROVENANCE
-> EMBED CHILD NODES -> TRANSACTIONAL PERSIST -> ACTIVATE DOCUMENT VERSION
```

## Engine decisions

| Concern | Decision |
|---|---|
| Primary parser | Docling — born-digital PDF + DOCX, no OCR |
| OCR fallback engine | PaddleOCR-VL-1.6 (`paddleocr>=3.7,<4`), pinned in P9B |
| Local OCR backend | llama-cpp-server GGUF Q4_K_M on GTX 1650 4GB, page-by-page |
| Strong-GPU path | Offline batch script (P9E) producing preparsed Markdown |
| Config fallback engine | PP-StructureV3 for CPU-only machines |
| Runtime V1 default | Scanned PDFs get typed `NEEDS_OCR`; inline OCR is config-gated OFF |
| Embeddings | `AITeamVN/Vietnamese_Embedding`, local snapshot, 1024 dims (ADR 0012) |
| Reranker | `namdp-ptit/ViRanker`, local snapshot (used by P10) |

The OCR engine sits behind the same `DocumentParser` Protocol as Docling;
retrieval, agents, and API contracts never see provider objects.

### P9B engine pin (ADR record)

```text
primary parser      : Docling (born-digital PDF + DOCX, pipeline do_ocr=False)
ocr fallback engine : PaddleOCR-VL-1.6 (pinned paddleocr>=3.7,<4; offline only)
local backend       : llama-cpp-server GGUF Q4_K_M, GTX 1650 4GB, page-by-page
strong-GPU path     : P9E offline batch script (engines: paddle / surya_2 /
                      ppstructurev3) feeding source_type=preparsed_markdown
config fallback     : PP-StructureV3 for CPU-only machines
runtime V1 default  : scanned PDFs -> typed NEEDS_OCR, queued for the offline
                      path; inline OCR fallback config-gated OFF
                      (PARSING__OCR_FALLBACK_ENABLED=false)
```

Implementation boundary (P9B): the parsing layer lives in
`app/services/ingestion/parsing/` behind the `DocumentParser` Protocol;
`ParsedDocument`/`ParseResult` domain types are the only surface visible to
P9C/P9D. Parse-quality gating (`evaluate_parse_quality`) turns degenerate
parses into typed `NEEDS_OCR` / `CORRUPT` statuses instead of embedding them.
The runtime suite stays green with no OCR model installed and never downloads
models during tests (docling engine is mocked in unit tests; DOCX conversion
is model-free and exercised for real).

## Sub-phase boundaries

```text
P9A foundation + contracts   (this document, SourceDocument, fingerprint,
                              idempotency state machine, type detection)
P9B parsing layer            (Docling/Markdown adapters, normalized tree,
                              quality checks, fixtures, OCR ADR pin)
P9C chunking engine          (parent/child/table strategies, deterministic IDs)
P9D embedding + orchestration(local embeddings, transactional persist, jobs,
                              incremental sync, observability, Review Pack)
P9E offline OCR batch script (independent; feeds source_type=preparsed_markdown)
```

Each sub-phase has its own approval gate.

## Ownership & tenancy (decision from the P9D review, binding on P10)

`documents.user_id` is nullable as of migration 0006: pipeline-level
ingestion has no end-user attribution. The V1 semantics are:

```text
user_id IS NULL  -> shared/system corpus (ingested from Drive sync or
                    preparsed batches; readable by any authorized user)
user_id IS NOT NULL -> user-private upload (P10 must filter by owner)
```

P10 entry requirement: retrieval queries MUST scope by `owner = requester OR
user_id IS NULL` once user-context retrieval exists. Row-level security and a
per-user Drive-sync attribution model remain future work; do not ingest
per-user private corpora before that decision is implemented.

## Idempotency contract

Fingerprints hash: `source_id`, content checksum, modified timestamp, size,
parser version, parent chunker version, child chunker version, embedding model,
embedding dimensions. Component version bumps therefore trigger reindexing.

Transitions:

```text
NEW       -> ingest
UNCHANGED -> skip
MODIFIED  -> create candidate version + reindex
DELETED   -> deactivate according to retention policy
FAILED    -> previous valid ACTIVE version stays searchable; retry allowed
```

Never remove the currently valid searchable version before its replacement is
successfully built.
