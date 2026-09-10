# CURRENT PHASE

Current phase: **P20 WAITING FOR USER REVIEW — V1 RELEASE (End-to-End Evaluation, Security Hardening & v1.0 Release).**
P9 family (P9A..P9E), P10 family (P10A..P10D), P11, P12, P13, P14, P15, P16, P17, P18, and P19
are approved and CLOSED as of 2026-09-09 (see ledger).

Read:
1. `MASTER_PLAN.md`
2. `phases/P20_end_to_end_evaluation_security_hardening_v1_release.md`
3. `reviews/P20_v1_release_report.md` (P20 Review Pack - V1 Release Report)
4. `docs/V1_RELEASE_NOTES.md` (v1.0 Production Release Notes)

Gate status: **P10 FAMILY CLOSED · P11 CLOSED · P12 CLOSED · P13 CLOSED · P14 CLOSED · P15 CLOSED · P16 CLOSED · P17 CLOSED · P18 CLOSED · P19 CLOSED · P20 WAITING FOR USER REVIEW — V1 RELEASE**

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
APPROVED P10A  received from user 2026-08-27 ("approved p10A") against
               reviews/P10A_review_pack.md v2 — closes P10A incl. its
               post-review fixes (migration 0008 HNSW index, citation-anchor
               metadata, METADATA_LOOKUP contract guard, alembic autogenerate
               filter). Historical note: P10A had been implemented before any
               formal `APPROVED P10` user message existed; treated as a
               process deviation, acknowledged and reconciled by this ledger
               plus the rewritten review pack (2026-08-27).
APPROVED P10B  received from user 2026-08-28 ("approved p10B") against
               reviews/P10B_review_pack.md v2 — closes P10B including all
               post-review fixes (H1..H4, M1..M5, L1..L4).
APPROVED P10C  received from user 2026-09-03 ("Approved P10c") against
               reviews/P10C_review_pack.md — closes P10C including all
               post-review fixes (H1..H4, M1..M4, M-NEW, L1..L3, L-NEW 1/2).
APPROVED P10   received from user 2026-09-03 ("ok approved p10") against
               reviews/P10D_review_pack.md and reviews/P10_review_pack.md —
               closes P10D and the entire P10 family (hybrid RRF + ViRanker
               factory wiring included). Unblocks Phase 11.
APPROVED P11   received from user 2026-09-07 ("appoived p11") against
                reviews/P11_review_pack.md — closes Phase 11 (Specialist Agent
                Runtime + Bounded ReAct). Unblocks Phase 12.
APPROVED P12   received from user 2026-09-07 ("approved p12") against
                reviews/P12_review_pack.md — closes Phase 12 (CommunicationAgent
                + CalendarAgent, incl. live-Google verification §4.2 and the
                People API host fix). Unblocks Phase 13.
APPROVED P13   received from user 2026-09-07 ("approved p13") against
                reviews/P13_review_pack.md — closes Phase 13 (KnowledgeResearchAgent,
                retrieval/web tools, read-only research boundary). Unblocks Phase 14.
APPROVED P14   received from user 2026-09-07 ("approved p14") against
                 reviews/P14_review_pack.md — closes Phase 14 (Skill System +
                 First Dynamic Skills). Unblocks Phase 15.
APPROVED P15   received from user 2026-09-08 ("ok approved p15") against
                 reviews/P15_review_pack.md — closes Phase 15 (Fast Triage +
                 Static Workflow Registry, §18A.5 LangGraph isolation in harness,
                 H1-H8, M1-M9, L1-L5, N1-N6 remediated, 61 P15 tests, 819 unit tests passing).
                 Unblocks Phase 16.
APPROVED P16   received from user 2026-09-09 ("ok vậy approved 16, làm phase 17 đi")
                 against reviews/P16_review_pack.md v3 — closes Phase 16 (Supervisor +
                 Dynamic Multi-Agent DAG, H1-H2, L1-L2 remediated, 5 non-blocking MEDIUM
                 debts triaged to P17/P18, 0 warnings, full suite green, ruff/pyright 0 errors).
                 Unblocks Phase 17.
APPROVED P17   received from user 2026-09-09 ("approved p17") against
                 reviews/P17_review_pack.md v2 — closes Phase 17 (Context Memory,
                 Entity Resolution, Memory Gate, Token-Pressure Compaction, H1 backfill hash key fix,
                 M1 runtime wiring, M2 ambiguous preservation, M3 gate fallback, M4 prompt sanitization,
                 L1-L3 fixes, 23 Phase 17 tests, full suite green 90% coverage across 13,321 stmts).
                 Unblocks Phase 18.
APPROVED P18   received from user 2026-09-09 ("oke approved p18") against
                 reviews/P18_review_pack.md — closes Phase 18 (PolicyEngine, Question Plane,
                 token-API approval plane, single-use token consumption, REST APIs,
                 16 security invariant tests passing, 0 pyright/ruff errors).
                 Unblocks Phase 19.
APPROVED P19   received from user 2026-09-09 ("approved p19") against
                 reviews/P19_review_pack.md — closes Phase 19 (Workflow Hardening +
                 Meeting Prep Graph WF-05, H1-H2, M1-M4 remediated, calibrated benchmark,
                 fail-closed 0 fabrication, 35 unit tests green, 0 ruff/pyright errors).
                 Unblocks Phase 20.
NEXT GATE      `WAITING FOR USER REVIEW — V1 RELEASE` (`APPROVED P20`) — closes Phase 20 based on phases/P20_end_to_end_evaluation_security_hardening_v1_release.md.
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
                Pack at reviews/P10A_review_pack.md (v2).
                **APPROVED & CLOSED 2026-08-27.**
          P10B  processing pipeline & expansion policies —
                **APPROVED & CLOSED 2026-08-28.** Review Pack at
                reviews/P10B_review_pack.md (v3).
          P10C  policies & safety — compare-doc, evidence/citation,
                sufficiency/retry, answer synthesis, prompt-injection
                boundary (P10-14..20; heaviest sub-phase) —
                **APPROVED & CLOSED 2026-09-03.** Review Pack at
                reviews/P10C_review_pack.md.
          P10D  benchmark dataset, ablations, latency tracing, family
                Review Pack (P10-21..26) — **APPROVED & CLOSED 2026-09-03.**
                Review Packs at reviews/P10D_review_pack.md and
                reviews/P10_review_pack.md. **P10 FAMILY CLOSED.**
           P11   Specialist Agent Runtime + Bounded ReAct —
                 **APPROVED & CLOSED 2026-09-07.** Review Pack at
                 reviews/P11_review_pack.md.
           P12   CommunicationAgent + CalendarAgent —
                 **APPROVED & CLOSED 2026-09-07.** Review Pack:
                 reviews/P12_review_pack.md (incl. live-Google verification
                 and People API host fix in §4.1/§4.2).
           P13   KnowledgeResearchAgent —
                 **APPROVED & CLOSED 2026-09-07.** Review Pack at
                 reviews/P13_review_pack.md.
           P14   Skill System + First Dynamic Skills —
                 **APPROVED & CLOSED 2026-09-07.** Review Pack at
                 reviews/P14_review_pack.md.
            P15   Fast Triage & Static Workflow Registry —
                 **APPROVED & CLOSED 2026-09-08.** Review Pack at
                 reviews/P15_review_pack.md.
            P16   Supervisor Dynamic Multi-Agent DAG —
                 **APPROVED & CLOSED 2026-09-09.** Review Pack at
                 reviews/P16_review_pack.md.
            P17   Context + Memory + Entity Resolution + Memory Gate —
                  **APPROVED & CLOSED 2026-09-09.** Review Pack at
                  reviews/P17_review_pack.md.
            P18   Policy + Approval Plane + Question Plane + Full Write Access —
                  **APPROVED & CLOSED 2026-09-09.** Review Pack at
                  reviews/P18_review_pack.md.
            P19   Workflow Hardening + Meeting Prep Graph (WF-05) —
                  IN PROGRESS. Spec: phases/P19_workflow_hardening_meeting_prep_graph.md.
```

---

## Pre-P12 Architectural & Governance Remediation (H1–H7 Complete)

Prior to issuing formal `APPROVED P11` and proceeding to Phase 12, all 7 identified HIGH-severity defects (H1–H7) have been remediated, verified, and reconciled:

- **H1 (OCR Sidecar Checksum & Provenance)**:
  - Fixed `scripts/ocr_batch.py`: dry-run now respects `should_skip` and never overwrites existing real sidecars; reports preserve relative paths; robust path resolution via `md_path_for_sidecar`.
  - Added `ocr_source_checksum` and `ocr_engine` to `FingerprintInputs` and canonical fingerprint hash.
  - Ingestion automatically detects and attaches companion `.ocr.json` sidecar.
  - Verified with 12 unit tests in `test_ocr_batch.py` and `test_ocr_provenance.py`.

- **H2 (RAG E2E Multi-Source Synthesis & Factory Wiring)**:
  - Removed `NotImplementedError` for `internal_only=False` in `synthesis.py`.
  - Added `SYNTHESIS_EXTERNAL_SYSTEM_PROMPT` separating `[Tài liệu nội bộ]` from `[Nguồn mở rộng]`.
  - Added `generate` callback parameter to `build_retrieval_pipeline` in `factory.py`.
  - Verified end-to-end with local embedding and ViRanker cross-encoder.

- **H3 (Mutation Tools Gated Fail-Closed Prior to P18)**:
  - Added `approval_token` to `ToolContext`.
  - Enforced fail-closed checks on all mutation tools in `GoogleCalendarTools`, `GoogleCommunicationTools`, and `GoogleDriveTools`: mutation execution without `approval_token` or `approval_id` raises `PermissionDeniedError`.
  - Verified with unit tests across calendar, communication, and drive tools.

- **H4 (Auth, Readiness, Spill & Secrets Hardening)**:
  - Closed dev/test auth fail-open: reject unauthenticated `X-User-ID` spoofing.
  - Added real dependency checks (DB, embedding dims, encryption keys) in health readiness probe.
  - Fixed Google OAuth reconnect: preserve existing refresh token when new token is None; revoke both access & refresh tokens on disconnect.
  - Hardened spill directory: full 64-char sha256 session hash, `0o700`/`0o600` permissions, direct slice trimming.
  - Added security warning logs on Fernet key file generation and chmod failures.

- **H5 (Concurrency Race & Job Tracking Durability)**:
  - Added row-level lock `with_for_update()` in `persist_candidate`: concurrent workers re-check fingerprint and exit cleanly without PK collisions.
  - Added `heartbeat()` to `IngestionJobStore` and updated orchestrator to avoid false stale recovery.
  - Only record ingestion jobs if creation succeeded.

- **H6 (Ingestion Data Loss Prevention)**:
  - Flushed unclosed code fences at EOF in `MarkdownDocumentParser`.
  - Extracted `PictureItem` captions and administrative metadata in `DoclingParser`.
  - Preserved orphan chunks (`parent_id is None`) during PARENT and NEIGHBORS expansion in `ExpansionService`.
  - Raised metadata sanitization limits to 32KB / 500 items in `_sanitize_document_metadata`.
  - Recorded structured failure reasons and warnings for `NEEDS_OCR` documents.

- **H7 (Governance Reconciliation & Downstream Spec Hardening)**:
  - Reconciled historical deviations (P9A, P10A, P9C) in `CURRENT_PHASE.md`.
  - Updated `MASTER_PLAN.md` §18A (DeepSeek harness patterns: `spill`, `repeat-guard`, `compaction`, `ask-user`), §18A.7 (Vector 1024 dimension migration), §23 (P10A-D split), and §37 (added missing execution cards for P9 and P10).
  - Completely rewrote and expanded downstream specifications: `P12`, `P13`, `P15`, `P19` with formal entry criteria, tool bindings, test matrices, and quantitative pass thresholds.
  - Resolved "KEEP AS SKILL" ambiguity in P19 by establishing the definitive compilation of `WF-05: MeetingPrepGraph`.

