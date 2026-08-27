# PHASE P9D REVIEW PACK — EMBEDDING + ORCHESTRATION

## 1. Phase objective

Wire parsing (P9B) and chunking (P9C) into the complete pipeline: local
embedding of child chunks, transactional persistence with atomic version
activation, background job execution, incremental synchronization, and
observability — proven end-to-end on fixtures, with the family Review Pack.

## 2. Tasks completed

- **P9D-1 EmbeddingService** (`app/services/ingestion/embedding.py`)
  - `LocalEmbeddingService` wraps a synchronous `EmbeddingBackend`, runs it
    via `asyncio.to_thread`, batches by `EMBEDDING__BATCH_SIZE`, retries
    transient engine failures with bounded backoff, and validates every
    vector against `EmbeddingContract(model, dimensions)` — mismatches raise
    typed `DimensionMismatchError` immediately (no retry, no silent mixing).
  - `TransformersPoolingBackend`: offline mean-pooling XLM-R backend loading
    the local snapshot via `local_files_only=True`; lazy torch/transformers
    import keeps them out of the runtime import graph.
  - V1 embeds CHILD/TABLE_CHILD only; model name recorded per vector row.
- **P9D-2 Transactional persistence**
  - Ports: `IngestionRepository` + `UnitOfWork` in
    `app/services/ingestion/persistence.py`.
  - Adapter: `app/infrastructure/db/ingestion_repository.py`. One
    transaction inserts candidate `documents` row + all chunk rows
    (parents as `node_type=PARENT/hierarchy_level=0`, children as
    `CHILD/TABLE_CHILD` level=1 with pgvector embeddings), then archives
    previous ACTIVE versions and flips the candidate ACTIVE. Any failure
    rolls the whole candidate back; the previous version stays searchable.
  - **Fingerprint persistence (P9A finding M2 closed):** migration 0006 adds
    `documents.fingerprint VARCHAR(64)` + `(logical_document_id,
    fingerprint)` index; the orchestrator hashes all nine spec inputs.
- **P9D-3 Background processing** (`jobs.py`)
  - `ingestion_jobs` table (migration 0006) mirrors the P3 audit-outbox
    claim pattern: `claimed_by`/`claimed_at`, stale-RUNNING recovery, status
    check constraint covering the eleven spec statuses.
  - `run_batch()` ingests sources independently; per-file failures produce
    FAILED records and never abort the batch. Durability is best-effort:
    store outages cannot block ingestion.
- **P9D-4 Incremental sync** — `IngestionOrchestrator.sync_decision()` maps
  stored-fingerprint comparison to NEW / MODIFIED / UNCHANGED;
  `deactivate_source()` implements deleted-source archiving policy (no hard
  delete). `preparsed_markdown` fingerprints flow through the same inputs.
- **P9D-5 Observability** — frozen `IngestionObservability` carries every
  spec field: ids, versions, sizes, parser/chunker/embedding names+versions,
  five stage durations, three counts, warnings, failure reason. Batch runner
  logs structured `ingestion_job_finished` events.
- **P9D-6 Integration tests** — `tests/integration/test_ingestion_pipeline.py`
  (live-PG skip pattern): markdown + DOCX end-to-end hierarchy queries,
  version bump/archival, failed-candidate protection, dimension-mismatch
  rollback (zero orphan rows), job-row recording.

## 3. Files created

- `app/domain/models/ingestion.py`
- `app/services/ingestion/embedding.py`
- `app/services/ingestion/persistence.py`
- `app/services/ingestion/orchestrator.py`
- `app/services/ingestion/jobs.py`
- `app/infrastructure/db/ingestion_repository.py`
- `alembic/versions/0006_ingestion_fingerprint_and_jobs.py`
- `scripts/inspect_ingestion.py`
- `tests/unit/services/test_ingestion_embedding.py` (8)
- `tests/unit/services/test_ingestion_orchestrator.py` (9)
- `tests/unit/services/test_ingestion_jobs.py` (1)
- `tests/integration/test_ingestion_pipeline.py` (8, live-PG gated)

## 4. Files modified

- `app/infrastructure/db/models.py`: `Document.fingerprint`,
  `Document.user_id` nullable (system ingestion), new `IngestionJobRecord`.
- `tests/unit/infrastructure/test_migrations.py`: head assertion → 0006 +
  new schema checks.
- `tests/integration/test_postgres_persistence.py`: head assertion → 0006.
- Plan docs: gate ledger updates.

## 5. Architecture decisions

- **Deterministic document IDs before insert** (`doc-<sha256(logical#vN)[:32]>`)
  so chunk identity (which needs document_version_id) exists pre-persist;
  removes the insert-order race and keeps P9C ID reproducibility intact.
- **Ports/adapters split** keeps the orchestrator hermetically unit-testable
  (InMemory repository + null transaction) while the SQLA adapter carries all
  SQL.
- **user_id relaxed to nullable**: pipeline-level ingestion has no end-user
  attribution; recorded as a deliberate schema decision (review-pack item).
- **Job claims on Postgres only for now**: Redis is not required for V1
  correctness; the claim pattern matches P3 semantics and one worker class
  suffices until multi-host scale demands Redis locks (deferred, tracked).

## 6. Public contracts changed

- New domain models (`IngestionStatus`, `IngestionObservability`,
  `StoredFingerprintState`); new public surface
  `app.services.ingestion.{embedding,persistence,orchestrator,jobs}`;
  schema additions above.

## 7. Database migrations

- `0006_ingestion_fingerprint_and_jobs`: fingerprint column + index,
  nullable user_id, `ingestion_jobs` table. Downgrade reverses fully.

## 8. Test results

- New P9D tests: 26 = 18 unit (embedding 8, orchestrator 9, jobs 1)
  + 8 integration.
- Full suite green; ruff clean; pyright 0 errors repo-wide.
- Integration: authored and collecting cleanly; live path skips when no
  PostgreSQL instance is reachable at :5434 (none available on this machine —
  no Docker/PG service installed). Same environment constraint as P3/P9A.

## 9. Manual inspection checklist (executed via scripts/inspect_ingestion.py)

```text
Documents inspected : 5 (runbook md, long_hierarchical docx, handbook docx,
                      budget docx, vendor-matrix md)
TOTAL parents = 28   (>= 10 required)
TOTAL children= 30   (>= 30 required)
TABLE examples= 3    (>= 2 required; header repetition verified, rows<=34/group)
```

Verified: semantic parents follow heading paths exactly; heading context line
present at parent raw start; children carry Section:-prefixed embedding text;
table groups repeat headers; parent-child linkage queryable by id.

## 10. OCR usage

- NEEDS_OCR path exercised in unit tests (typed status, nothing persisted);
  scanned.pdf fixture validated in P9B manual run. No OCR performed inline.

## 11. Re-index behavior

- Unchanged fingerprint -> SKIPPED (no second document row).
- Changed checksum/metadata -> MODIFIED -> version N+1 candidate; activation
  swaps ACTIVE atomically; old version archived (searchable history kept).
- Model/dimension change -> loud DimensionMismatchError; reindex requires
  explicit migration per contract.

## 12. Security/privacy notes

- No network calls anywhere in the pipeline (embeddings offline-only).
- Job payloads carry source ids/metadata only, never file bytes.

## 13. Known limitations / deferred

- Live-PG integration execution pending an available instance (skips clean;
  superseded — executed green, see §16).
- Redis-based multi-worker claiming deferred (single Postgres claim pattern).
- **LOW-3 (post-review)**: stale-claim recovery is time-based only
  (`claimed_at` vs 900s default) — a CPU-bound embed of a very large document
  can outlive the bound and be double-claimed by a future second worker.
  Single-worker V1 is unaffected; scale-out must raise the bound or add claim
  heartbeats first (noted in `claim_pending`).
- **LOW-4 (post-review)**: the fingerprint check (`latest_state`) runs outside
  the persist transaction, so two concurrent ingests of one source can both
  elect version N+1; the `documents` PK makes the loser fail loud instead of
  corrupting state. A per-logical-document advisory lock is the scale-out
  follow-up (noted at the call site in `orchestrator.py`).
- Real-model embedding smoke test deferred to first deployment with GPU/CPU
  budget; backend interface already proven by adapter tests + calibration
  benchmark from the P9C review (superseded — H1 real-model smoke passed,
  see §16).

## 14. Deviations from plan

- None functional. Three recorded choices: Postgres-only job claims (above),
  and `documents.user_id` made nullable for system-level ingestion.
- **LOW-2 (post-review, accepted deviation)**: spec P9D-3 states "Job
  infrastructure uses the existing Redis + Postgres foundations"; the
  implementation exercises only the Postgres foundation (claim pattern).
  Redis locks are unnecessary for V1 single-worker correctness and are
  deferred as tracked scope — disclosed in §5/§13.

## 15. Gate status: APPROVED P9D — received from user 2026-08-25
("APPROVED P9D và APPROVED P9E")

## 16. Post-review round — live-PG execution + fix ledger

Supersedes the environment statements in §8 ("no PostgreSQL reachable") and
§13 ("live-PG pending"): a real stack became available
(assistant_postgres `pgvector/pgvector:pg16` @ :5434 + Redis) and every
previously-skipped integration path was executed for real.

- **H1 (pooling backend)** — `TransformersPoolingBackend` switched to CLS
  (first-token) embeddings per the model's shipped `1_Pooling/config.json`
  instead of mean-pooling. Real-model smoke against the local Vietnamese
  snapshot: dim=1024, L2 norm=1.0, self-cosine=1.0.
- **M1 closed (execution)** — integration suite on live PostgreSQL: **8/8
  PASS** (version bump/archival, failed-candidate rollback on real pgvector).
- **M2 closed (PDF e2e)** — new `test_pdf_end_to_end_via_real_docling` runs
  cached Docling models over real PDF fixtures: text PDF → COMPLETED with
  persisted parents/children; scanned.pdf → typed NEEDS_OCR, zero rows.
- **M3 (ownership decision)** — recorded in
  `docs/architecture/document-ingestion-strategy.md`: NULL user_id = shared
  corpus; P10 retrieval MUST scope owner (note added to CURRENT_PHASE).
- **M5 + L-round fixes** — fingerprint nullable rows reingest as MODIFIED (no
  version-1 collision); dead `next_version` removed; SKIPPED surfaces as
  warnings (failure_reason stays clean); recursive json_safe payload;
  `attempt_count` increments only on FAILED; downgrade restores NOT NULL;
  parent rows dropped `embedding_model`; `claim_pending` uses
  `FOR UPDATE SKIP LOCKED` on PG; chunker version bumped 1.1.0 (reindex-safe);
  public `docling_version()` import.
- **Live-run adapter bugs caught & fixed** — new `SqlAlchemyUnitOfWork`
  bridges `async_sessionmaker` (lacks `.transaction()`); `AsyncSession` moved
  out of TYPE_CHECKING to a runtime import; missing `IngestionStatus` import
  in `jobs.record()` (NameError swallowed by best-effort try/except left job
  rows QUEUED) and class-body import placement in
  `ingestion_repository.py` (pyright undefined names) — all fixed.
- **Final gate** — ruff check/format clean, pyright 0 errors repo-wide,
  pytest full suite 448 passed, coverage 89%. (Corrected during the
  2026-08-25 full-P9 review: an earlier draft of this line said 456 — a
  miscount of the progress dots; the pre-P9E suite was 448.)

### 16.1 Findings round (user review: MEDIUM-1, LOW-1..LOW-4)

- **MEDIUM-1 fixed** — repo-wide `ruff check .` added to gate evidence
  (previous rounds scoped `app tests` only): migration 0006 import order
  fixed via `ruff --fix`; unrelated scratch debug scripts
  (`scripts/inspect_oc.py`, `scripts/test_9router.py` — muse-spark/9router,
  not part of P9D) removed from the tree; `0006_*.py` +
  `scripts/inspect_ingestion.py` reformatted to close the widened
  `ruff format` scope.
- **LOW-1 fixed** — test counts corrected in §3/§8: orchestrator has 9 unit
  tests (not 11); P9D total = 26 = 18 unit + 8 integration.
- **LOW-2 accepted & recorded** — spec-text deviation documented verbatim in
  §14 with rationale.
- **LOW-3/LOW-4 documented** — known limitations recorded in §13 with inline
  code notes at `claim_pending` (`jobs.py`) and the `latest_state` call site
  (`orchestrator.py`).

Post-findings gate (all repo-wide, re-executed after every fix above):
`ruff check .` clean · `ruff format --check` clean (147 files) · pyright
0 errors · pytest full suite **448 passed** (P9D-era total, pre-P9E;
18 P9D unit + 8 P9D integration live-PG included).

