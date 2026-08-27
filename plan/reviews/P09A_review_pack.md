# PHASE P9A REVIEW PACK — INGESTION FOUNDATION + CONTRACTS

> Generated retroactively during the P9B review cycle (finding H1): P9A was
> implemented in the working tree before this pack existed. This document
> records what shipped, the schema-verification findings required by P9A-5,
> and the deliberate deferrals.

## 1. Phase objective

Freeze the ingestion strategy, define source-neutral contracts and the
fingerprint/versioning state machine, and verify persistence readiness — all
without any parsing dependency.

## 2. Tasks completed

- **P9A-1: Strategy freeze** — `docs/architecture/document-ingestion-strategy.md`
  written: canonical structure-aware hierarchical parent–child strategy,
  canonical flow, sub-phase boundaries, engine-decision table (OCR ADR pointer
  landed in P9B), 1024-dim local embedding decision referencing ADR 0012.
- **P9A-2: Source abstraction** — `SourceDocument` frozen Pydantic model in
  `app/domain/models/documents.py` (`source_id`, `source_type` literal of
  drive/upload/local_fixture/preparsed_markdown, filename, MIME, mtime, size,
  checksum, external_uri, metadata). Helpers `checksum_bytes` /
  `checksum_file` / `build_local_source_document` in
  `app/services/ingestion/source.py` (streaming SHA-256).
- **P9A-3: Fingerprint, versioning, idempotency** — pure function
  `compute_fingerprint(FingerprintInputs) -> DocumentFingerprint` hashing all
  nine spec inputs including parser/chunker/embedding versions;
  `decide_ingest_action(stored, incoming)` implementing NEW / UNCHANGED /
  MODIFIED / DELETED / RETRY_AFTER_FAILURE transitions while preserving the
  previous ACTIVE version on failure. No I/O anywhere in these paths.
- **P9A-4: Type detection** — `detect_document_type()` combining declared
  MIME + extension + magic-byte content sniffing; typed `UNSUPPORTED` result
  instead of exceptions. During the P9B review this was hardened further:
  ambiguous zip/text sniffs now require extension/MIME corroboration
  (`text/plain` no longer implies Markdown), so mislabeled payloads surface as
  UNSUPPORTED rather than downstream CORRUPT.
- **P9A-5: Persistence readiness check** — verified against
  `app/infrastructure/db/models.py` and migrations 0001/0002/0005. Findings
  recorded below (section "P9A-5 findings"); no migration was required for
  the fields the pipeline needs today.

## 3. Files created

- `docs/architecture/document-ingestion-strategy.md`
- `app/domain/models/documents.py`
- `app/services/ingestion/__init__.py`, `fingerprint.py`, `source.py`,
  `type_detection.py`, `versioning.py`
- `alembic/versions/0005_embedding_dimensions_1024.py` (pgvector dim pin)
- `tests/unit/domain/test_document_models.py`
- `tests/unit/services/test_ingestion_fingerprint.py`,
  `test_ingestion_source.py`, `test_ingestion_type_detection.py`,
  `test_ingestion_versioning.py`

## 4. Files modified

- `app/core/config.py`: `EmbeddingSettings` / `RerankerSettings` groups.
- `app/infrastructure/db/models.py`: pgvector `Vector(1024)` columns,
  `DocumentChunk` hierarchy fields.
- `plan/CURRENT_PHASE.md`: phase tracking.

## 5. Architecture decisions

- Fingerprint = SHA-256 over a canonical JSON projection of all versioned
  inputs; component versions are injected config so upgrades force reindex.
- State machine is a pure decision layer; side effects belong to P9D
  orchestration (never delete-before-replace).
- Detection returns typed results, never raises.

## 6. Public contracts changed

- Added `SourceType`, `DetectedDocumentType`, `DetectionStatus`,
  `IngestDecision`, `SourceDocument`, `FingerprintInputs`,
  `DocumentFingerprint`, `TypeDetectionResult`, `StoredSourceState`.

## 7. Database migrations

- `0005_embedding_dimensions_1024` pins vector dimensions to 1024 (ADR 0012).

## 8. Tests added

- fingerprint stability (same inputs → same hash; each input change changes it);
- idempotency transitions incl. FAILED-preserves-active;
- SourceDocument validation (blank filename, negative size, frozen model);
- detection matrix incl. mismatch warnings and unsupported typed status.

39 tests total across the four files listed above.

> Post-review update (2026-08-25, full-P9 review): after the P9B L-fix round
> added detection tests, the actual surface is **5 test files with 58
> collected tests** (models 17, fingerprint 12, source 6, type_detection 16,
> versioning 7). The "39 / four files" figure above is preserved as the
> historical snapshot at pack generation time.

## 9. Test results (as of P9B review)

- All P9A unit tests pass within the full suite; ruff + pyright clean repo-wide.

## 10. P9A-5 findings (required reporting)

- **Finding M1 — no `document_versions` table (accepted deviation).**
  The spec names tables `documents`, `document_versions`, `document_chunks`.
  The implemented schema keeps one `documents` table where each row IS a
  version: `logical_document_id` groups versions, `version_number` orders
  them, and a partial unique index `uq_documents_one_active_version`
  (`logical_document_id` WHERE `is_active`) enforces exactly one active
  version per logical document. Functional coverage of the spec's intent
  (candidate versions, single searchable active version) is complete; the
  naming deviation is recorded here per the review.
- **Finding M2 — composite fingerprint has no storage column (deliberate
  deferral to P9D).** The schema stores `source_content_hash`; the full
  fingerprint (which additionally mixes parser/chunker/embedding versions) is
  currently computed in-memory only. End-to-end idempotency cannot run until
  P9D persists either the composite fingerprint or its inputs (candidates:
  dedicated `fingerprint` column on `documents`, or a
  `document_source_state` table mirroring `StoredSourceState`). Tracked as a
  P9D entry-criterion item so it cannot be silently skipped.
- Chunk-level fields required by later phases are present post-0005:
  `hierarchy_level`, `parent_id`, `heading_path`, `chunk_index`,
  `page_start/page_end`, `token_count`, `content_hash`, `embedding_model`,
  `embedding_dimensions`.

## 11. Security/privacy notes

- Checksums hash content only; no secrets enter fingerprints or logs.

## 12. Known limitations

- See finding M2 (persistence wiring deferred).
- Detection corroboration policy tightened in P9B review; see
  `reviews/P09B_review_pack.md` post-review section.

## 13. Gate status

- Implementation complete; gate `APPROVED P9A` was never sent as a formal
  user message before P9B work began (gate-discipline note H2). Reconciled in
  `plan/CURRENT_PHASE.md`: the user's explicit "Approve P8 làm P9B" instruction
  authorized proceeding through P9B on top of this reviewed state.
