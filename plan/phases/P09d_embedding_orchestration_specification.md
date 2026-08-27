> Active phase file for P9D. Read `../MASTER_PLAN.md` and the P9 overview first.
> Do not implement until the user sends `APPROVED P9D`.

# P9D — EMBEDDING + ORCHESTRATION — EXECUTION SPECIFICATION

## Objective

Wire parsing (P9B) and chunking (P9C) into the complete pipeline: local
embedding of child chunks, transactional persistence with version activation,
background job execution, incremental synchronization, and observability — then
prove it end-to-end on fixtures and assemble the family Review Pack.

## Entry criteria

```text
[ ] P9C APPROVED
[ ] local models present: models/models--AITeamVN--Vietnamese_Embedding (1024 dims),
    models/models--namdp-ptit--ViRanker (reranker used by P10, config only here)
[ ] PostgreSQL + pgvector running (schema at 1024 dims, migration 0005)
```

---

## P9D-1 — Embedding service (was P9-17)

```python
class EmbeddingService(Protocol):
    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]: ...

    async def embed_query(
        self,
        text: str,
    ) -> list[float]: ...
```

Requirements:

- loads `AITeamVN/Vietnamese_Embedding` from `models/` via
  `EMBEDDING__LOCAL_PATH` (`local_files_only=True`, no network);
- batch calls; bounded retry; dimension validation against
  `EMBEDDING__DIMENSIONS` (1024);
- model/version recorded per persisted vector;
- NO silent mixing of incompatible dimensions/models;
- default V1 embeds CHILD/TABLE_CHILD units only;
- embedding model changes require explicit reindex/migration.

## P9D-2 — Transactional persistence (was P9-18)

Preferred sequence:

```text
create ingestion job -> candidate document version -> parsed document tree
-> parents -> children/table children -> child embeddings -> candidate validation
-> atomically mark candidate ACTIVE -> deactivate previous version
```

If indexing fails: previous ACTIVE version remains searchable.

## P9D-3 — Background processing (was P9-19)

Long ingestion runs outside request/response. Statuses:

```text
QUEUED RUNNING PARSING BUILDING_PARENTS BUILDING_CHILDREN
EMBEDDING PERSISTING COMPLETED FAILED SKIPPED NEEDS_OCR
```

One failed file must not abort a batch. Job infrastructure uses the existing
Redis + Postgres foundations (outbox/claims patterns from P3).

## P9D-4 — Incremental synchronization (was P9-20)

```text
source metadata -> compare fingerprints
   NEW      -> ingest
   MODIFIED -> reingest as new candidate version
   SAME     -> skip
```

Deleted sources are deactivated according to policy rather than hard-deleted.
`preparsed_markdown` sources fingerprint using their sidecar checksum (P9E).

## P9D-5 — Ingestion observability (was P9-21)

Every job exposes:

```text
job_id, source_id, document_id, document version, source size,
parser/version, parent chunker/version, child chunker/version,
embedding model/version,
parse duration, parent-build duration, child-build duration,
embedding duration, persist duration,
parent count, child count, table child count,
warnings, failure reason
```

## P9D-6 — Integration tests + manual inspection (was P9-23 integration/manual)

Integration:

- PDF parse -> parents -> children -> embeddings -> persisted hierarchy query;
- DOCX same;
- Markdown source same;
- modified file creates new version; unchanged file skips;
- failed candidate keeps previous ACTIVE version;
- batch failure isolation;
- dimension-mismatch embedding rejected loudly.

Manual inspection:

```text
>= 3 document trees, >= 10 parent chunks, >= 30 child chunks,
>= 2 table chunk examples
```

Verify semantic boundaries, source anchors and parent-child linkage.

## P9D-7 — Family Review Pack (was P9-24)

Review Pack MUST include:

```text
parent/child configuration
parent count/document, child count/document
sample hierarchy + heading paths
sample raw_text vs embedding_text
table examples
OCR usage (incl. NEEDS_OCR cases)
re-index behavior
warnings/failures
sub-phase gate status P9A..P9D (+ P9E if done)
```

---

## Definition of Done

```text
[ ] EmbeddingService implemented (local 1024-dim model, offline)
[ ] transactional persist + atomic version activation implemented
[ ] background jobs with statuses + batch isolation implemented
[ ] incremental sync implemented
[ ] observability fields emitted
[ ] integration tests green incl. live PostgreSQL path
[ ] manual inspection checklist executed
[ ] full suite green (ruff + pyright + pytest)
[ ] Review Pack generated at reviews/P09_review_pack.md
[ ] user APPROVED P9E next (or records explicit deferral)
```
