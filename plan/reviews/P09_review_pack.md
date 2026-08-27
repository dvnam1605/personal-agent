# PHASE P9 FAMILY REVIEW PACK — DOCUMENT INGESTION PIPELINE

Assembled per P9D-7. Detailed packs: `P09A_review_pack.md`,
`P09B_review_pack.md`, `P09C_review_pack.md`, `P09D_review_pack.md`.

## 1. Parent/child configuration (V1 benchmark start)

```text
parent target 1600 tokens / hard max 2400   (ChunkingSettings)
child target  500 tokens / hard max 800
small-node merge threshold 40 tokens
token estimator: structural-aware chars/4, calibrated vs the production
XLM-R tokenizer (see P09C review, finding M1); P9D re-benchmark entry item
```

## 2. Counts (manual inspection corpus, scripts/inspect_ingestion.py)

```text
documents=5   parents=28   children=30   table_children=3
per-document: runbook 3p/4c · hierarchy docx 18p/18c · handbook 3p/3c ·
              budget docx 1p/1c · vendor-matrix 3p/4c
```

## 3. Sample hierarchy + heading paths

```text
Deployment Runbook > Preconditions   -> PARENT par-0d15… (children: paragraph + TABLE_CHILD)
Deployment Runbook > Rollout         -> PARENT par-ae40…
Deployment Runbook > Rollout > Rollback -> PARENT par-32f1…
Part 1 > Chapter 1.1 > Section 1.1.1 -> PARENT par-90d4… (docx real parser)
```

## 4. raw_text vs embedding_text sample

```text
raw        : "Confirm the release tag exists and CI is green before starting."
embedding  : "Section: Deployment Runbook > Preconditions\n\nConfirm the release tag…"
```

Enrichment is heading-path only; no claims added; no LLM rewriting.

## 5. Table examples

- runbook step table: single TABLE_CHILD, header preserved.
- vendor-matrix 40-row table: two TABLE_CHILD row groups, each repeating
  `| Vendor | Tier | Cost | Notes |` header; rows partitioned without loss.

## 6. OCR usage

- Inline OCR OFF by design; scanned PDFs produce typed `NEEDS_OCR`
  (validated on `scanned.pdf`, unit-covered) and queue for P9E.
- Corrupt PDFs produce typed `CORRUPT`; batches continue.

## 7. Re-index behavior

- UNCHANGED fingerprint → SKIPPED (single row).
- MODIFIED → candidate version N+1; atomic ACTIVE swap; previous archived.
- Failed candidate leaves previous ACTIVE searchable (unit + integration).
- Embedding model/dimension mismatch → loud typed error; explicit migration
  required.

## 8. Warnings/failures observed in test runs

- parse warnings propagate from adapters into job records;
- batch isolation proven (COMPLETED/FAILED/COMPLETED pattern);
- failure reasons carry engine messages (`unsupported input`, `dimension
  mismatch`, parse failures);
- live-PG integration executed green: 8/8 pass on real PostgreSQL+pgvector
  (:5434), including PDF e2e via real cached Docling models and scanned.pdf
  → typed NEEDS_OCR; `ingestion_jobs` rows recorded with final COMPLETED
  status (see P09D pack §16).

## 9. Sub-phase gate status

```text
P9A APPROVED-equivalent (retroactive reconciliation; see ledger)
P9B APPROVED 2026-08-24
P9C APPROVED 2026-08-24
P9D APPROVED 2026-08-25 (live-PG integration executed green, 8/8)
P9E APPROVED 2026-08-25 (offline OCR batch script)
```

Family DoD: **COMPLETE** — all five sub-phases approved; P9 family closed
2026-08-25. Live-PG integration execution was closed by the P9D post-review
round. Next: P10 (retrieval with owner scoping), gate `APPROVED P10`.
