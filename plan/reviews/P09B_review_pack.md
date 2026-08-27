# PHASE P9B REVIEW PACK — PARSING LAYER

## 1. Phase objective

Turn raw bytes (PDF/DOCX/Markdown) into a provider-independent normalized
document tree behind a `DocumentParser` Protocol, with parse-quality gating,
typed failure statuses (`NEEDS_OCR` / `CORRUPT`), and the frozen OCR engine
decision. Docling becomes a real dependency; nothing above this layer sees a
Docling type.

## 2. Tasks completed

- **P9B-1: Parser adapter (Protocol + Docling + Markdown)**
  - `DocumentParser` Protocol in `app/services/ingestion/parsing/base.py`
    matching the spec signature exactly (`async parse(source, content) -> ParsedDocument`),
    verified at runtime via `runtime_checkable`.
  - `DoclingDocumentParser` (born-digital PDF + DOCX): lazy engine import,
    module-level converter cache, PDF pipeline built with `do_ocr=False`,
    bytes fed through docling `DocumentStream`, conversion executed off-loop
    via `asyncio.to_thread`. Engine failures raise typed `DocumentParseError`.
  - `MarkdownDocumentParser`: pure-Python ATX headings, paragraphs, bullet and
    numbered lists, pipe tables, fenced code blocks preserved verbatim;
    non-UTF8 raises typed `DocumentParseError`.
  - `build_parser_for_type()` routes by detected type; `preparsed_markdown`
    sources always route to the Markdown parser regardless of filename.
- **P9B-2: Canonical normalized document tree**
  - `app/domain/models/parsed_document.py`: frozen Pydantic discriminated
    union — `HeadingNode` / `ParagraphNode` / `ListNode` / `TableNode` under
    `ParsedNode` with `node_id`, `order`, `text`, `heading_path`,
    `SourceAnchor(page_start, page_end, block_index)`. Tables carry
    `markdown` rendering plus `row_count`/`column_count`; lists keep their
    item texts. Root `ParsedDocument` carries typed `ParsedDocumentMetadata`
    (parser name/version, page count, OCR flag, parser warnings).
- **P9B-3: Parse quality checks**
  - `evaluate_parse_quality()` pure function producing the spec'd report:
    `text_length, node_count, heading_count, table_count, empty_page_count,
    ocr_used, parse_warnings`.
  - Reject rules: empty tree, insufficient useful text on large sources,
    extreme garbage ratio (replacement/control chars). Broken parses return
    typed statuses so a batch continues per file; nothing corrupt is embedded.
- **P9B-4: OCR strategy decision**
  - Flow implemented in `parse_source()`: detect → parse → gate.
    Insufficient text from the docling path yields typed `NEEDS_OCR`;
    runtime V1 keeps inline OCR OFF (`ParsingSettings.ocr_fallback_enabled=false`)
    and appends the "queued for offline OCR path (P9E)" warning.
  - Engine decision recorded as an ADR update inside
    `docs/architecture/document-ingestion-strategy.md` ("P9B engine pin"):
    primary Docling; fallback PaddleOCR-VL-1.6 pinned `paddleocr>=3.7,<4`;
    local backend llama-cpp-server GGUF Q4_K_M; strong-GPU path = P9E batch
    script; config fallback PP-StructureV3; scanned PDFs get `NEEDS_OCR`.
  - New typed settings group `ParsingSettings` in `app/core/config.py`
    (thresholds + OCR policy), documented in `.env.example`.
- **P9B-5: Fixture corpus**
  - `scripts/make_parsing_fixtures.py`: deterministic dependency-free
    generator (hand-built minimal PDF writer + OOXML zip writer).
  - Generated corpus committed under `tests/fixtures/parsing/`:
    `pdf/` normal_text, multicolumn, table_heavy, long_sections, scanned
    (image-only); `docx/` headings_paragraphs, lists, tables,
    long_hierarchical (with styles.xml so Heading styles map to section
    headers); `markdown/preparsed_sample.md` (P9E output shape);
    `failure/` corrupt.pdf (truncated xref) + unsupported.bin.

## 3. Files created

- `app/domain/models/parsed_document.py`
- `app/services/ingestion/parsing/__init__.py`
- `app/services/ingestion/parsing/base.py`
- `app/services/ingestion/parsing/docling_parser.py`
- `app/services/ingestion/parsing/markdown_parser.py`
- `app/services/ingestion/parsing/quality.py`
- `scripts/make_parsing_fixtures.py`
- `tests/fixtures/parsing/**` (12 fixture files)
- `tests/unit/services/test_ingestion_parsed_document_models.py`
- `tests/unit/services/test_ingestion_markdown_parser.py`
- `tests/unit/services/test_ingestion_parse_quality.py`
- `tests/unit/services/test_ingestion_docling_parser.py`
- `tests/unit/services/test_ingestion_parse_source.py`
- `plan/reviews/P09B_review_pack.md` (this document)

## 4. Files modified

- `pyproject.toml` / `uv.lock`: added `docling>=2.59,<3` (resolved 2.121.0)
  as a real runtime dependency per spec entry criteria.
- `app/core/config.py`: new `ParsingSettings`; registered as
  `Settings.parsing`.
- `.env.example`: documented `PARSING__*` knobs.
- `app/services/ingestion/__init__.py`: re-exported parsing surface.
- `app/services/ingestion/type_detection.py`: `_vote` return annotation fixed
  to `DetectedDocumentType | None`; set-comprehension simplified (lint).
- `docs/architecture/document-ingestion-strategy.md`: P9B engine pin ADR.
- `plan/CURRENT_PHASE.md`: gates updated (APPROVED P8 recorded; current P9B).

## 5. Architecture decisions

- **Provider isolation**: Docling objects are imported lazily and only inside
  `docling_parser.py`; a reflection subprocess test asserts that importing
  the public `app.services.ingestion` package never pulls in `docling`.
- **Typed failures over exceptions above the layer**: engines may raise, but
  `parse_source()` normalizes everything into `ParseResult` statuses
  (`UNSUPPORTED` / `CORRUPT` / `NEEDS_OCR` / `PARSED`) so one bad file cannot
  abort a batch (spec shared invariant).
- **Title semantics**: a detected document title anchors the heading stack
  (never popped by same-level section headers); sections nest by level so
  `heading_path` stays stable for P9C chunk provenance.
- **List merging**: consecutive docling list items merge into one `ListNode`
  preserving item order and text.
- **No model downloads in tests**: unit tests mock the engine with real
  docling-core item classes; DOCX conversion is model-free and exercised for
  real offline; heavy PDF model execution was validated manually once.

## 6. Public contracts changed

- Added `app.domain.models.parsed_document` types (tree, quality report,
  parse statuses).
- Added `app.services.ingestion.parsing` public surface (`DocumentParser`,
  adapters, `parse_source`, `evaluate_parse_quality`).
- Added `ParsingSettings` config group (nested env prefix `PARSING__`).

## 7. Database migrations

- None required for P9B (persistence remains P9D; schema verified ready in
  P9A including migration 0005).

## 8. Tests added

- `test_ingestion_parsed_document_models.py` (10): discriminated union of all
  node kinds, frozen immutability, ListNode content invariant, heading level
  bounds, anchor bounds, metadata defaults/warnings, ParseResult status
  invariants (PARSED requires document+quality; UNSUPPORTED requires reason).
- `test_ingestion_markdown_parser.py` (8): full fixture structure (headings,
  table rows/cols, list items), heading-path hierarchy, numbered lists,
  fenced code preservation, empty input, invalid UTF-8 typed error.
- `test_ingestion_parse_quality.py` (7): happy path, report counts +
  warning passthrough + OCR flag, large-source NEEDS_OCR reason,
  small-parse NEEDS_OCR, markdown empty-tree CORRUPT, garbage-ratio CORRUPT,
  threshold configurability, empty-page accounting from anchors.
- `test_ingestion_docling_parser.py` (9): mocked-engine tree mapping incl.
  title/section/list/table, heading paths, page anchors + empty-page warning,
  engine failure typed error, partial-success warnings, picture skipping,
  real offline DOCX e2e, docling no-leak reflection (subprocess),
  preparsed_markdown routing.
- `test_ingestion_parse_source.py` (8): end-to-end markdown PARSED,
  unsupported typed result, preparsed override, engine failure → CORRUPT,
  unexpected exception → CORRUPT, OCR queue warning when inline fallback OFF,
  no queue warning when ON, real DOCX fixture through the pipeline,
  routing table, Protocol conformance of both adapters.

## 9. Test results

- **New P9B tests**: 42 passed.
- **Full suite**: green — 0 failures; only pre-existing skips (2 PostgreSQL
  integration tests requiring the local container).
- **Lint** (`ruff check app tests`): All checks passed.
- **Format** (`ruff format --check`): formatted.
- **Typecheck** (`pyright`, whole repo): 0 errors, 0 warnings.

## 10. Manual verification (real engines)

Real docling run against generated fixtures (models cached locally):

```text
pdf/normal_text.pdf   -> PARSED      heading + paragraphs, page anchors
pdf/table_heavy.pdf   -> PARSED      positioned grid cells extracted
pdf/multicolumn.pdf   -> PARSED      two-column text merged correctly
pdf/scanned.pdf       -> NEEDS_OCR   empty tree; queued-for-P9E warning
failure/corrupt.pdf   -> CORRUPT     engine error surfaced as typed failure
```

## 11. LLM-call/latency observations

- Zero LLM calls in the parsing layer.
- Conversion runs in a worker thread (`asyncio.to_thread`); the event loop is
  never blocked during engine work.

## 12. Security/privacy notes

- No network calls during parsing tests; docling weights load from local
  cache only when explicitly exercised outside CI.
- Parse results carry text content only; raw source bytes are not retained on
  any domain model.

## 13. Known limitations

- Hand-built minimal fixtures do not exercise docling's layout-model table
  classification (borderless positioned cells come back as paragraphs); real
  bordered Word/PDF exports classify as TableItem and are covered by the
  mocked walker tests.
- DOCX page numbers are absent by design (backend provides none); heading
  provenance is the required signal per spec.
- Section-header levels from some backends default to level 2 when the
  backend omits explicit levels; hierarchy still monotonic and deterministic.

## 14. Deferred items

- Inline OCR-capable parser adapter (PaddleOCR/Surya) lands with P9E/offline
  machine work; the Protocol already accepts it without changes above.
- Actual chunk consumption of `heading_path` is P9C.

## 15. Deviations from plan

- None functionally. Two recorded choices: (a) docling added to main project
  dependencies (not optional extras) because the spec makes it a real
  dependency in this sub-phase, while imports stay lazy so import-graph cost
  is zero until first use; (b) PDF-model-dependent execution is validated
  manually rather than in CI to keep the runtime suite hermetic.

## 16. Diff summary

- Parsing package (5 modules) + parsed-document domain models (1 module).
- 12 committed fixtures + deterministic generator script.
- 42 new unit tests; full suite, ruff, pyright green.
- Config/docs/plan updates listed in section 4.

## 17. Suggested reviewer focus

- `app/services/ingestion/parsing/base.py`: status normalization and
  per-file isolation guarantees.
- `app/services/ingestion/parsing/quality.py`: gating thresholds and the
  NEEDS_OCR-vs-CORRUPT boundary (decided by detected document type, not
  parser naming).
- `app/services/ingestion/parsing/docling_parser.py`: heading-stack
  semantics and provider-type containment.
- `tests/unit/services/test_ingestion_docling_parser.py`: no-leak reflection
  test strength.

## 18. Post-review fixes (2026-08-24 review findings)

- **H1** — `reviews/P09A_review_pack.md` created retroactively, including the
  P9A-5 schema findings (M1/M2) that had been unreported.
- **H2** — `plan/CURRENT_PHASE.md` rewritten with a single gate ledger;
  the missing formal `APPROVED P9A` message is acknowledged as a process
  defect reconciled by review, covered by the user's explicit P9B
  authorization.
- **M1 / M2** — recorded as accepted deviation (versioned `documents` table,
  no separate `document_versions`) and tracked deferral (composite
  fingerprint persistence) in the P9A pack + CURRENT_PHASE P9D note.
- **M3** — enabling `PARSING__OCR_FALLBACK_ENABLED=true` now appends an
  explicit warning that no inline OCR adapter exists yet (engine ships with
  P9E/offline path); it can no longer be mistaken for working OCR.
  Covered by `test_no_queue_warning_when_inline_fallback_enabled`.
- **L1** — dead conditional removed (`winner = candidates[0]`).
- **L2** — sniff corroboration policy: zip-derived DOCX and text-derived
  MARKDOWN require extension/MIME agreement; `text/plain` no longer implies
  Markdown; conflicting/uncorroborated sniffs return typed UNSUPPORTED with
  an explanatory warning instead of a guess. `preparsed_markdown` sources now
  bypass detection entirely. Tests updated accordingly
  (`test_zip_sniff_without_corroboration_is_unsupported`,
  `test_plain_text_without_markdown_signal_is_unsupported`,
  `test_generic_zip_with_docx_extension_is_docx_candidate`).
- **L3** — NEEDS_OCR decision no longer keyed off parser naming:
  `evaluate_parse_quality(..., ocr_capable_parse=...)` is derived from the
  detected document type in the orchestrator.
- **L4** — short-but-valid documents no longer fail the text gate; rejection
  requires an empty tree or a large source with almost no text.
  Covered by `test_short_but_valid_document_passes`.
- **L5** — converter cold-start guarded by `threading.Lock`; title stack level
  unified to root sentinel 0 in both title branches; unused ProvenanceItem
  import removed.
- **L6** — `build_local_source_document` types `source_type: SourceType`;
  `# type: ignore` dropped.
- **L7** — GFM table separator regex relaxed to single-dash cells
  (`| - | - |` detected); setext headings remain unsupported by design
  (P9E controls preparsed output shape). Covered by
  `test_short_table_separator_still_detected`.

Post-fix totals: 63 tests across the six ingestion service test files; full
suite green; ruff + pyright clean repo-wide.

> Post-review update (2026-08-25, full-P9 review): collected counts for the
> five parsing files are now **47 tests** (parsed models 9, markdown 8,
> quality 11, docling 9, parse_source 10) — later fix rounds added cases to
> quality/parse_quality and parse_source beyond §8's original per-file
> numbers, which are preserved above as history.

## 19. Gate status: APPROVED P9B — received from user 2026-08-24 ("approved p9b")
