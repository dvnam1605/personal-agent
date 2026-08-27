> Active phase file for P9A. Read `../MASTER_PLAN.md` and
> `P09_document_ingestion_pipeline_execution_specification.md` (overview) first.
> Do not implement until the user sends `APPROVED P9A`.

# P9A — INGESTION FOUNDATION + CONTRACTS — EXECUTION SPECIFICATION

## Objective

Freeze the ingestion strategy, define source-neutral contracts and the
fingerprint/versioning state machine, and verify persistence readiness — all
without any parsing dependency.

## Entry criteria

```text
[ ] P8 APPROVED
[ ] PostgreSQL + pgvector available (running locally)
[ ] Redis/job infrastructure available
[ ] Drive file abstraction exists (P8)
[ ] embedding provider configuration contract exists (EmbeddingSettings, 1024 dims)
[ ] tracing/audit foundation exists
```

If a prerequisite is missing, report it instead of introducing a hidden
alternative stack.

---

## P9A-1 — Freeze the ingestion strategy (was P9-00)

Create:

```text
docs/architecture/document-ingestion-strategy.md
```

Canonical flow (unchanged from original spec):

```text
SOURCE DISCOVERY -> SOURCE SNAPSHOT/FINGERPRINT -> TYPE DETECTION
-> PARSE WITH DOCLING -> NORMALIZE DOCUMENT TREE -> PARSE QUALITY CHECK
-> BUILD SEMANTIC PARENT NODES -> BUILD RETRIEVAL CHILD NODES
-> TABLE-SPECIFIC CHUNKING -> CHUNK ENRICHMENT + PROVENANCE
-> EMBED CHILD NODES -> TRANSACTIONAL PERSIST -> ACTIVATE DOCUMENT VERSION
```

The document must record: strategy choice, sub-phase boundaries, OCR engine
decision pointer (ADR lands in P9B), and the 1024-dim local embedding decision
(ADR 0012).

## P9A-2 — Source abstraction (was P9-01)

Source-neutral contract:

```python
class SourceDocument(BaseModel):
    source_id: str
    source_type: Literal["drive", "upload", "local_fixture", "preparsed_markdown"]
    filename: str
    mime_type: str | None
    modified_at: datetime | None
    size_bytes: int | None
    checksum: str | None
    external_uri: str | None
    metadata: dict[str, Any]
```

Parser code must not care whether bytes came from Drive, upload, or fixtures.
`preparsed_markdown` sources skip the OCR fallback path entirely.

Expected modules:

```text
app/services/ingestion/source.py
app/domain/models/documents.py
```

## P9A-3 — Fingerprint, versioning and idempotency (was P9-02)

Stable fingerprint inputs, where available:

```text
source_id, checksum, modified timestamp, size,
parser version, parent chunker version, child chunker version,
embedding model/version
```

Required state transitions:

```text
NEW       -> ingest
UNCHANGED -> skip
MODIFIED  -> create candidate version + reindex
DELETED   -> deactivate according to retention policy
FAILED    -> preserve previous valid ACTIVE version
```

Never remove the currently valid searchable version before its replacement is
successfully built. Fingerprint computation must be a pure function with unit
tests (no I/O).

## P9A-4 — File type detection (was P9-03)

Never trust file extension alone. Combine:

```text
declared MIME + extension + parser validation
```

Supported V1:

```text
PDF
DOCX
(+ Markdown for preparsed_markdown sources)
```

Unsupported input returns a typed `UNSUPPORTED` status instead of crashing a batch.

## P9A-5 — Persistence readiness check

Verify the existing P3 schema (`documents`, `document_versions`,
`document_chunks` after migration 0005) covers hierarchy fields required later:
`level`, `parent_id`, `heading_path`, `chunk_index`, `page_start/page_end`,
`token_count`, `content_hash`, `embedding_model`, `embedding_dimensions`.

If anything is missing, produce an Alembic migration in this sub-phase.
Report findings in the Review Pack; do not defer schema gaps to P9D.

---

## Required tests

- fingerprint stability (same inputs => same fingerprint; any input change changes it);
- idempotency state machine transitions (NEW/UNCHANGED/MODIFIED/DELETED/FAILED);
- SourceDocument validation;
- type detection matrix (PDF/DOCX/MD accepted; mismatched MIME+extension flagged; unsupported typed status).

## Definition of Done

```text
[ ] strategy ADR written
[ ] SourceDocument contract implemented
[ ] fingerprint pure function + state machine implemented
[ ] type detection implemented
[ ] persistence readiness verified (migration if needed)
[ ] unit tests green; full suite green (ruff + pyright + pytest)
[ ] Review Pack section generated
[ ] user APPROVED P9B next
```
