# CURRENT PHASE

Current phase: **P10A implemented, self-reviewed and hardened — awaiting the
formal user gate.** The P9 family (P9A..P9E) is fully approved and CLOSED as
of 2026-08-25. P10A work began without a recorded `APPROVED P10` message;
the deviation is acknowledged below (same handling as the P9A precedent) and
the sub-phase must NOT proceed to P10B before an explicit `APPROVED P10A`.

Read:
1. `MASTER_PLAN.md`
2. `phases/P09_document_ingestion_pipeline_execution_specification.md` (overview)
3. Next phase spec when its gate is approved.

Gate status: **P9 FAMILY CLOSED · P10A IMPLEMENTED & SELF-REVIEWED — WAITING FOR USER REVIEW — P10A**

## Gate ledger (single source of truth)

```text
APPROVED P8    received from user 2026-08-24 ("Approve P8 làm P9B đi").
               The same instruction explicitly authorized implementing P9B.
APPROVED P9A   never sent as a formal user message. P9A was implemented in
               this working tree before the formal gate flow was followed;
               the deviation is acknowledged and reconciled by review:
               plan/reviews/P09A_review_pack.md now exists (finding H1) and
               documents its schema findings (M1/M2) and deferrals.
APPROVED P9B   received from user 2026-08-24 ("approved p9b").
APPROVED P9C   received from user 2026-08-24 ("làm p9c đi"); closed after
               post-review fixes (see P09C pack §16).
APPROVED P9D   received from user 2026-08-24 ("approved p9c làm p9d đi");
               implemented + self-reviewed + post-review fixes (pack §16,
               §16.1) incl. live-PG integration 8/8; formally APPROVED by
               user 2026-08-25 ("APPROVED P9D và APPROVED P9E").
APPROVED P9E   received from user 2026-08-25 ("ok appproved p9e");
               implemented + reviewed same day; formally APPROVED by user
               2026-08-25 ("APPROVED P9D và APPROVED P9E") — closes the P9
               family.
NEXT GATE      `APPROVED P10A`. Historical note: P10A was implemented before
               a formal `APPROVED P10` user message was ever recorded; per the
               MASTER_PLAN "no phase skipping" invariant this is treated as a
               process deviation, acknowledged and reconciled by this ledger
               entry plus the rewritten review pack (2026-08-27). No P10B work
               may start until its own explicit gate message arrives.
```

Invariant note (MASTER_PLAN "no phase skipping"): the P9A gap is treated as a
process defect, corrected retroactively via the review packs above; no further
sub-phase starts without its explicit `APPROVED P9x` message.

## What P9B delivered

`DocumentParser` Protocol + Docling/Markdown adapters, provider-neutral
normalized document tree, parse-quality gating with typed
`NEEDS_OCR`/`CORRUPT` statuses, OCR engine ADR pin, fixture corpus under
`tests/fixtures/parsing/`, full suite green (pytest + ruff + pyright).

Review packs:

- `reviews/P09A_review_pack.md`
- `reviews/P09B_review_pack.md`

(Historical: P9C..P9E subsequently proceeded under their own approved gates —
see the ledger above.)

---

## Phase family status

```text
P0..P7   approved (see MASTER_PLAN and git history)
P8       approved 2026-08-24
P9A      implemented; gate reconciled retroactively (see ledger above)
P9B      APPROVED 2026-08-24 (closed)
P9C      APPROVED 2026-08-24 (closed after post-review fixes)
P9D      APPROVED 2026-08-25 (closed; live-PG integration executed 8/8,
         post-review fixes in reviews/P09D_review_pack.md §16/§16.1)
P9E      APPROVED 2026-08-25 (closed; reviews/P09E_review_pack.md) —
         P9 FAMILY CLOSED
P10       split approved (see P10 spec header); ownership entry requirement
          met — retrieval scopes by owner
          (user_id = requester OR user_id IS NULL) per
          docs/architecture/document-ingestion-strategy.md.
          P10A  retrieval foundation & core search — IMPLEMENTED +
                self-reviewed + post-review fixes (independent review session
                2026-08-27): migration 0008 HNSW ANN index, citation-anchor
                metadata on candidates, METADATA_LOOKUP contract guard,
                alembic autogenerate filter. Verified green:
                pytest full unit suite / ruff / pyright. Rewritten Review
                Pack at reviews/P10A_review_pack.md. GATE OPEN — awaiting
                explicit `APPROVED P10A`.
          P10B  processing pipeline & expansion policies (06..13) — blocked
                until `APPROVED P10B`
          P10C  policies & safety — compare-doc, evidence/citation,
                sufficiency/retry, answer synthesis, prompt-injection
                boundary (14..20; heaviest sub-phase)
          P10D  benchmark dataset, ablations, latency tracing, family
                Review Pack (21..26)
```
