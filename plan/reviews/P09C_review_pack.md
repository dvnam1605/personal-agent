# PHASE P9C REVIEW PACK — CHUNKING ENGINE

## 1. Phase objective

Implement the canonical V1 chunking strategy: structure-aware hierarchical
parent–child chunking with dedicated table semantics, deterministic IDs, and
explicit raw_text vs embedding_text separation. Pure functions over
`ParsedDocument`; no I/O, no embedding calls.

## 2. Tasks completed

- **P9C-1: Strategy Protocols** — `ParentChunkingStrategy` /
  `ChildChunkingStrategy` in `protocols.py` match the spec signatures exactly
  (`runtime_checkable`). Identity inputs + budgets are constructor-injected
  via the frozen `ChunkContext` dataclass and `ChunkingSettings`, keeping the
  Protocol methods pure transforms.
- **9.1 Parents** — `SectionParentChunker`: consecutive same-`heading_path`
  nodes form a section; sections never mix unrelated headings; smaller-than-
  target natural sections stay intact; oversized sections split greedily at
  node (paragraph-group) boundaries against the real joined-token size; a
  single oversized node falls back sentence-packing → hard token cut.
- **9.2 Children** — `SentenceChildChunker` builds children strictly inside
  one parent's raw_text: paragraph/list segment boundaries first, sentences,
  token-cut last resort; children inherit parent identity/heading/page/block
  anchors.
- **9.3 Index rule** — children are the embedded units; parent drafts carry
  text for expansion but no embedding fields beyond the placeholder
  `embedding_model` passthrough (V1 adds no parent embeddings).
- **9.4 Overlap** — zero overlap emitted in V1 (boundaries already semantic);
  covered by a non-duplication test between child texts.
- **9.5 Small-node merging** — adjacent tiny CHILD units merge within the same
  parent while the combined size stays under the child hard max.
- **P9C-2: Hierarchy representation** — frozen typed drafts
  (`app/domain/models/chunks.py`) carrying id, document_id,
  document_version_id, source_id, title, level (`PARENT|CHILD|TABLE_CHILD`),
  parent_id, heading_path, chunk_index/ordinal, raw_text, embedding_text
  (children), page anchors, source_block_ids, token_count, content_hash,
  chunker versions. Relations queryable without reparse.
- **P9C-3/P9C-4: Deterministic IDs** — `identity.py` hashes canonical JSON of
  spec ingredients (`par-<32hex>` / `chl-<32hex>`); same inputs → same IDs;
  any ingredient change → new ID. No random UUIDs.
- **P9C-5: raw vs embedding text** — children store both; enrichment is only
  the `"Section: A > B\n\n"` prefix derived from the existing heading path —
  no new claims, no LLM rewriting.
- **P9C-6: Table strategy** — pipe-table segments become TABLE_CHILD units:
  one unit when it fits the budget; large tables split into row groups where
  every group repeats the header (+separator) lines; all table children link
  to the surrounding section parent; no arbitrary character splitting.
- **P9C-7: Metadata contracts** — enforced by the typed draft models
  (required fields present; content_hash validated as sha256 hex).
- **Normalization step** — `tokens.normalize_text` implements exactly the
  allowed set (whitespace normalize, zero-width/parser artifact removal);
  forbidden operations are structurally impossible.

## 3. Files created

- `app/domain/models/chunks.py`
- `app/services/ingestion/chunking/__init__.py`
- `app/services/ingestion/chunking/protocols.py`
- `app/services/ingestion/chunking/tokens.py`
- `app/services/ingestion/chunking/identity.py`
- `app/services/ingestion/chunking/parents.py`
- `app/services/ingestion/chunking/children.py`
- `app/services/ingestion/chunking/engine.py` (`build_chunk_drafts`,
  `ChunkDraftSet`)
- `tests/unit/services/chunking_testkit.py` (typed test helpers)
- `tests/unit/services/test_ingestion_chunking.py` (20 tests)
- `plan/reviews/P09C_review_pack.md`

## 4. Files modified

- `app/core/config.py`: new `ChunkingSettings` (parent/child targets and hard
  maxes, small-merge threshold) registered as `Settings.chunking`.
- `app/services/ingestion/__init__.py`: exported chunking surface.

## 5. Architecture decisions

- **Token estimator**: deterministic chars/4 approximation instead of a
  tokenizer dependency; budgets in settings are calibrated to it. Swapping in
  a real tokenizer later is a version bump of the chunker IDs (reindex by
  design), not an interface change.
- **Children built from parent.raw_text**: keeps the spec Protocol signatures
  untouched and makes "child never crosses parent" true by construction;
  anchors/blocks are inherited per rule 9.2 rather than re-derived.
- **Tables as segments inside the parent**: detected from pipe+separator
  shape during child building so tables stay attached to their surrounding
  semantic parent without extra parent-level machinery.
- **No overlap in V1**: rule 9.4 permits it; dedup stays trivial.

## 6. Public contracts changed

- Added `ChunkLevel`, `ParentChunkDraft`, `ChildChunkDraft` domain models.
- Added `app.services.ingestion.chunking` public surface (strategies, engine,
  context, drafts) and `ChunkingSettings` config group.

## 7. Database migrations

- None required. All draft fields map onto the verified schema
  (`documents.logical_document_id/version_number/is_active`,
  `document_chunks.hierarchy_level/node_type/parent_id/heading_path/
  chunk_index/page_start/page_end/token_count/content_hash/...`).
  Mapping itself lands with P9D persistence.

## 8. Tests added

`tests/unit/services/test_ingestion_chunking.py` (20 tests):

- parent boundary preservation across headings; preamble as own parent;
- oversized-section splitting at paragraph groups under hard max;
- child containment within parent text; heading/page anchor inheritance;
- deterministic parent and child IDs (repeat-stability + sensitivity);
- ID format checks (`par-`/`chl-` prefixed, fixed length);
- tiny-node merging into one child within a parent;
- sentence fallback ladder for a giant single paragraph (ordered indexes,
  budget respected);
- unbreakable-string token-cut last resort;
- small table = single TABLE_CHILD linked to its section parent;
- large table row groups repeating headers, rows partitioned not duplicated;
- table node metadata preserved (row/column counts);
- raw vs embedding separation incl. no-prefix case without headings;
- normalization keeps structure, collapses excess whitespace;
- V1 emits no overlapping/duplicated child texts;
- parent and child metadata contracts (incl. filename/mime/source_type/
  embedding_model passthrough on children).

## 9. Test results

- Chunking tests: 20 passed.
- Full suite green (only pre-existing PostgreSQL-container skips);
  ruff check clean; pyright 0 errors repo-wide.

## 10. Sample chunks (fixture `markdown/preparsed_sample.md`)

```text
parents=3 children=4

PARENT par-edc6c457eda79eb471472ea9a1465ae8 ordinal=0
       path=Deployment Runbook>Preconditions tokens=46
  CHILD chl-3e2c35a500e6ae848c59112260b406b3[0] CHILD        tokens=16
        emb='Section: Deployment Runbook > Preconditions'
        raw='Confirm the release tag exists and CI is green...'
  CHILD chl-dd22253ffedb59e8ec1de0d788f7f71e[1] TABLE_CHILD  tokens=29
        emb='Section: Deployment Runbook > Preconditions'
        raw='| Step | Command | Owner |\n| --- | --- | --- |\n| Build | ...'

PARENT par-7dbf202435410904956cb82ca7f228a9 ordinal=1
       path=Deployment Runbook>Rollout tokens=32
  CHILD chl-2e1775a95c33fd016f9c6ea3fa1a8838[0] CHILD tokens=32

PARENT par-f13a431da6b455bc2cf1c44fd330940d ordinal=2
       path=Deployment Runbook>Rollout>Rollback tokens=14
  CHILD chl-617b013603566ff6e26298f7368e944e[0] CHILD tokens=14
```

## 11. Security/privacy notes

- No I/O, no network, no model calls anywhere in the engine; drafts carry
  document-derived text only.

## 12. Known limitations

- Token counts are estimates (chars/4); Vietnamese text runs slightly denser
  than English — budgets remain safe because hard maxes bound construction,
  not measurement precision.
- Setext-style Markdown headings are not recognized upstream (documented in
  P09B); preparsed sources therefore control their own output shape.
- Parent embeddings intentionally absent (spec 9.3).

## 13. Deferred items

- Persistence mapping of drafts onto `documents` / `document_chunks`
  (including the composite fingerprint deferral tracked in
  `reviews/P09A_review_pack.md`, finding M2) — P9D.

## 14. Deviations from plan

- None functional. One naming note: internal greedy fill tracks exact joined
  token size (join overhead included) so the hard max holds byte-exact.

## 15. Suggested reviewer focus

- `chunking/parents.py`: section grouping semantics, segment anchors, and the
  split ladder.
- `chunking/children.py`: boundary priority, table detection/row grouping,
  per-segment provenance, merge rule interplay with budgets.
- `chunking/identity.py`: ingredient completeness for reproducibility.

## 16. Post-review fixes (2026-08-24 review findings)

- **M1 (token estimator)** — benchmarked against the REAL XLM-RoBERTa
  tokenizer shipped with `AITeamVN/Vietnamese_Embedding` (local snapshot,
  offline). Measured chars/4 ratios: Vietnamese prose 0.95–0.97x (estimator
  was already conservative), English prose ~1.08x, table/markdown structure
  **up to ~1.75x** — confirming the risk is concentrated in structural glyphs,
  not Vietnamese syllables. `estimate_tokens` now prices pipes at weight 2 and
  heading/list glyphs at weight 1 on top of a 1.08 prose safety factor
  (`tokens.py`, calibration documented in-module). New tests pin the
  properties (structural > prose; Vietnamese sample ≥ real count). P9D entry
  item: re-run the benchmark against the production tokenizer before locking
  V1 budgets; chunker version bump reindexes if coefficients change.
- **M2 (child provenance)** — parents now carry `segment_anchors`
  `(block_id, page_start, page_end)` aligned by index with raw segments;
  children anchor to their own segment(s) instead of the whole-parent union;
  merged children union their anchors. Tests cover both split and merged
  paths.
- **L1** — explicit `separator_index is None or == 0` guard (no truthiness);
  headerless separator tables stay one unit. Tested.
- **L2** — single oversized rows stay atomic TABLE_CHILDs (never character-
  split); they consciously exceed budget rather than corrupt semantics.
  Tested.
- **L3** — hard cut returns `(prefix, consumed_before_strip)`; remainder is
  sliced losslessly in both ladders. Character-level losslessness tested.
- **L4** — `ChildChunkDraft.filename` required (`min_length=1`),
  `ChunkContext.filename` required; mime_type/source_type remain optional as
  preparsed_markdown sources legitimately lack canonical values (recorded
  relaxation).
- **L5** — parent raw_text now starts with the section heading path as its
  first segment (synthetic anchor block_id=None) restoring expansion context;
  children skip that segment (retrieval precision comes from the Section:
  prefix). Tested both sides.
- **L6** — greedy fill uses cached `TextMetrics` + O(1)
  `estimate_from_metrics(joiners=n)` arithmetic instead of rescanning joins.
- **L7** — parent field stays `ordinal` (matches spec ingredient "parent
  ordinal"); child keeps `chunk_index`. Naming documented here as intentional.

Post-fix totals: 29 chunking tests; full suite green; ruff + pyright clean
repo-wide.

## 17. Gate status: APPROVED P9C — received from user 2026-08-24
("làm p9c đi"); closed after post-review fixes (§16)
