> Active phase family for P9. Read `../MASTER_PLAN.md` first.
> P9 is split into five sequential sub-phases (P9A–P9E). Each has its own gate:
> do not implement a sub-phase until the user sends its approval
> (e.g. `APPROVED P9A`).

# P9 — DOCUMENT INGESTION PIPELINE — SPLIT OVERVIEW

## Objective (unchanged)

Create a production-shaped ingestion pipeline for a large and changing PDF/DOCX
corpus, preserving document hierarchy so P10 can retrieve small, precise CHILD
chunks while expanding to larger PARENT context only when useful.

Canonical V1 strategy: **structure-aware hierarchical parent-child chunking.**

## Why the split

The original single-phase spec covered 24 task areas. It is split so each part
has a short DoD, an isolated test gate, and fails fast at lower layers before
higher layers stack on top. Task IDs (P9-00 … P9-24) are preserved for traceability.

## Sub-phase map

| Sub | File | Gate | Content | Original tasks |
|---|---|---|---|---|
| P9A | `P09a_ingestion_foundation_contracts_specification.md` | `APPROVED P9A` | Strategy ADR, source abstraction, fingerprint/versioning/idempotency, type detection, schema verification | P9-00..03 |
| P9B | `P09b_parsing_layer_specification.md` | `APPROVED P9B` | Parser Protocol + Docling adapter, normalized tree, quality checks, Markdown parser, fixtures, OCR engine ADR | P9-04..07, 22 |
| P9C | `P09c_chunking_engine_specification.md` | `APPROVED P9C` | Parent/child/table chunking strategies, deterministic IDs, raw vs embedding text, metadata contracts | P9-09..16 |
| P9D | `P09d_embedding_orchestration_specification.md` | `APPROVED P9D` | EmbeddingService (local VN models), transactional persistence, background jobs, incremental sync, observability, E2E integration tests, Review Pack | P9-17..21, 23, 24 |
| P9E | `P09e_offline_ocr_batch_script_specification.md` | `APPROVED P9E` | Offline OCR batch script for strong-GPU machine; independent of the main pipeline path | P9-07A |

Dependency order: P9A -> P9B -> P9C -> P9D. P9E is independent and may run any
time after P9A (its output feeds `source_type="preparsed_markdown"`).

## Shared invariants (apply to every sub-phase)

```text
- Docling/OCR-specific objects never leak into retrieval, agents, or API contracts.
- Final retrieval/answer behavior belongs to P10; nothing here retrieves.
- Every sub-phase ends green: ruff + pyright + full pytest suite pass.
- No phase skipping without explicit user approval.
```

## P9 Definition of Done (family-level)

All five sub-phase DoDs checked plus:

```text
[ ] P9A APPROVED
[ ] P9B APPROVED
[ ] P9C APPROVED
[ ] P9D APPROVED
[ ] P9E APPROVED (or explicitly deferred by user)
[ ] family Review Pack generated (assembled by P9D)
```

Do NOT start P10 before all approvals are recorded.
