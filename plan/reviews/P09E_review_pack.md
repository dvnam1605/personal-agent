# PHASE P9E REVIEW PACK — OFFLINE OCR BATCH SCRIPT

## 1. Phase objective

A standalone script converts a FOLDER of scanned PDFs into a FOLDER of
Markdown files (`scripts/ocr_batch.py`), running OUTSIDE the assistant runtime
on a strong-GPU machine as a one-shot offline batch — never in the request
path. Output feeds ingestion through `source_type="preparsed_markdown"` with
the sidecar checksum driving fingerprinting (P9A-3 unchanged).

## 2. Tasks completed

- **P9E-1 Script contract** — `scripts/ocr_batch.py`: `--input-dir`,
  `--output-dir`, `--engine {paddleocr_vl_1_6|paddle_ppstructurev3|surya_2}`,
  `--device cuda:0|cpu`, plus `--recursive`, `--force`, `--dry-run`.
  Mirrored-tree naming `<stem>.md` + `<stem>.ocr.json`; page-by-page
  processing bounds VRAM; one failed PDF records a per-file error and never
  aborts the batch; batch report written to `ocr_batch_report.json`.
- **Sidecar contract** — pydantic model `OcrSidecar` (extra=forbid):
  schema_version, source_file, source_checksum (sha256 hex), engine,
  engine_version, device, ocr_used, pages_processed, warnings, duration,
  dry_run, finished_at UTC.
- **Idempotency** — re-runs skip outputs whose sidecar checksum matches the
  input PDF AND whose Markdown exists AND which was not merely planned by a
  dry-run; `--force` overrides.
- **Dry-run mode** — walks inputs, computes checksums and writes *planned*
  sidecars (`dry_run=true`, zero pages) WITHOUT Markdown and without touching
  engine imports; a later real run re-processes those files.
- **P9E-2 Dependency isolation (option a)** — optional dependency groups
  `[project.optional-dependencies] ocr-paddle` / `ocr-surya` for the external
  machine only; paddle/surya import lazily inside engine constructors so CI
  and the runtime suite never touch them. Post-review (2026-08-25): the
  `ocr-paddle` floor was aligned to the P9B ADR pin `paddleocr>=3.7,<4`
  (full-P9 review finding F1).
- **P9E-3 Verification** — 8 unit tests with faked engines cover arg parsing,
  sorted discovery, mirrored naming (flat + nested), sidecar schema (incl.
  committed fixture + extra-field rejection), idempotent skip rules (checksum
  change / dry-run-only sidecar / missing md), per-file failure isolation,
  dry-run end-to-end via `main()` incl. exit codes (0 ok, SystemExit(2) usage).
- **Fixture reuse** — committed sample pair `tests/fixtures/ocr/scanned_sample.md`
  + `.ocr.json` whose checksum equals the real `scanned.pdf` fixture (P9B),
  tying the OCR output contract to the pipeline's NEEDS_OCR input.
- **Docs** — README section "Offline OCR Batch (P9E, external GPU machine
  only)" documents install groups, invocation, and ingestion hand-off.

## 3. Files created

- `scripts/ocr_batch.py`
- `tests/fixtures/ocr/scanned_sample.md`
- `tests/fixtures/ocr/scanned_sample.ocr.json`
- `tests/unit/scripts/test_ocr_batch.py` (8)

## 4. Files modified

- `pyproject.toml`: optional groups `ocr-paddle`, `ocr-surya`.
- `README.md`: offline OCR usage section.
- `plan/CURRENT_PHASE.md`: gate ledger entry APPROVED P9E (2026-08-25).

## 5. Architecture decisions

- **Checksum single-source-of-truth**: the script imports
  `app.services.ingestion.source.checksum_file` (streaming sha256) instead of
  duplicating hashing, guaranteeing sidecar checksums match what the
  orchestrator fingerprints at ingest time.
- **Lazy engine loading**: heavy paddle/surya imports happen only inside the
  engine call path when NOT dry-running; `OcrEngine.load()` resolves the dist
  name and version via `importlib.metadata`, failing with a clear message if
  the optional group is missing (exit code 2).
- **Planned-sidecar semantics**: dry-run artifacts are explicitly marked
  `dry_run=true` and never satisfy the idempotent-skip rule, so planning
  cannot poison later real runs.
- **Exit codes**: 0 = all processed/skipped/planned, 1 = any file failed
  (batch still completes), 2 = configuration error (bad dir, missing engine).

## 6. Public contracts changed

- None in `app/`. New repo-level tooling surface: `scripts/ocr_batch.py` CLI
  + sidecar JSON schema (documented in-module via `OcrSidecar`).

## 7. Database migrations

- None.

## 8. Test results

- New P9E unit tests: 8 (all green; engines faked, no paddle/surya needed).
- Full suite after P9E: **456 passed** (448 prior + 8 new, collector-
  verified); ruff check/format clean repo-wide; pyright 0 errors.
  (Corrected during the full-P9 review — an earlier draft said 464.)
- Dry-run executed against the real fixture tree in-test through `main()`.

## 9. Security/privacy notes

- No network calls anywhere: engines load weights from local cache; the
  script performs no downloads.
- Sidecars carry checksums/metadata only, never document text beyond the
  Markdown output itself.

## 10. Known limitations / deferred

- Engine glue (`_paddle_pages` / `_surya_pages`) targets documented
  paddleocr 3.x / surya-ocr 0.17+ APIs and is smoke-validated on the external
  GPU machine per spec P9E-3 ("full engine execution happens on the external
  machine"); this environment verifies everything around it (fakes).
- Surya path hardcodes English language hint (`[["en"]]`) for V1.
- No parallel workers (sequential per-file loop) — sufficient for one-shot
  batches; VRAM safety favors page-by-page over concurrency.

## 11. Deviations from plan

- None functional. Sidecar filename chosen as `<stem>.ocr.json` (spec left
  naming to implementation); report file `ocr_batch_report.json` added for
  operator visibility.

## 12. Gate status: APPROVED P9E — received from user 2026-08-25
("APPROVED P9D và APPROVED P9E"). Closes the P9 family.
