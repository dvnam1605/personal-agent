# P10A Review Pack: Retrieval Foundation & Core Search

> **Rewritten 2026-08-27.** The original pack (2026-08-26) misdescribed the
> deliverables in several places and could not serve as a trustworthy review
> artifact; it was replaced by this version after an independent review pass
> that also applied four post-review code fixes (listed in §9).
> `plan/CURRENT_PHASE.md` was reconciled in the same session.

## 1. Summary

P10A establishes the retrieval foundation over the P9 hierarchical
parent–child index:

- typed retrieval contracts (`RetrievalMode`, `ExpansionPolicy`,
  `RetrievalQuery`, `RetrievedChunk`);
- child-level **dense** retrieval (pgvector cosine `<=>`, L2-normalised
  embeddings from the P9D `LocalEmbeddingService`);
- child-level **sparse** retrieval (PostgreSQL FTS on the generated
  `search_vector` column, `vietnamese_simple` config with unaccent mapping,
  queried via `websearch_to_tsquery`);
- **parallel hybrid fusion** with Reciprocal Rank Fusion (`k = 60`),
  dense and sparse legs executed concurrently via `asyncio.gather`;
- ownership scoping enforced centrally: every candidate query filters
  `d.is_active AND (documents.user_id = requester OR documents.user_id IS NULL)`
  (P9D-M3 rule; `requester_id IS NULL` sees only shared documents);
- zero LLM calls anywhere in the layer ("No LLM when code is enough").

## 2. Actual deliverables (verified against the working tree)

New files:

```text
app/domain/models/retrieval.py               # P10-01/02 contracts + validators
app/services/retrieval/__init__.py           # public surface
app/services/retrieval/sql.py                # shared SQL blocks + scoping filters + anchor metadata helper
app/services/retrieval/provider.py           # RowProvider protocol + SqlAlchemyRowProvider (shared settings-driven engine)
app/services/retrieval/dense.py              # DenseRetrievalService (spec P10-03)
app/services/retrieval/sparse.py             # SparseRetrievalService (spec P10-04)
app/services/retrieval/hybrid.py             # HybridRetrievalService, parallel RRF (spec P10-05)
alembic/versions/0007_add_fts_to_document_chunks.py   # unaccent ext + vietnamese_simple config + generated search_vector tsvector + GIN index
alembic/versions/0008_add_hnsw_embedding_index.py     # HNSW vector_cosine_ops ANN index for dense search (post-review fix)
docs/architecture/rag-retrieval-strategy.md           # P10-00 strategy document (all 12 MUST items)
tests/unit/services/test_retrieval_foundation.py      # 16 unit tests
```

Note: there is no `app/services/retrieval/query.py`; the contracts live in
`app/domain/models/retrieval.py` (the original pack listed it wrongly).

## 3. Spec traceability (P10-00..05)

| Task | Requirement | Where satisfied |
|---|---|---|
| P10-00 | strategy ADR/doc | `docs/architecture/rag-retrieval-strategy.md` §1–§13 (12/12 MUST items; later sub-phase behaviour marked P10B/C/D) |
| P10-01 | modes | `RetrievalMode` enum incl. `METADATA_LOOKUP`; METADATA_LOOKUP is rejected at the contract boundary so metadata questions can never enter RAG |
| P10-02 | RetrievalQuery | frozen model with suggested fields + `requester_id`; `search_query` defaults to `original_query` (no LLM rewrite); DOCUMENT_SEARCH / COMPARE_DOCUMENTS require document_ids |
| P10-03 | dense child search | CHILD/TABLE_CHILD only (`hierarchy_level = 1`), active-version + owner scoped; returns chunk_id, parent_id, document_id, content_raw, score, dense_rank + citation-anchor metadata |
| P10-04 | sparse child search | same scoping/candidate shape; `ts_rank_cd` scoring, rank 1 = best |
| P10-05 | parallel RRF fusion | `fusion_score = Σ 1/(k + rank)` per contributing source; tracks dense_rank/sparse_rank/fusion_score/retrieval_sources; no cross-score normalisation |

## 4. Migration story (corrected)

The DB change is delivered by **Alembic migration 0007** (idempotent,
PostgreSQL-guarded, sqlite-safe skip): `unaccent` extension,
`public.vietnamese_simple` configuration (copy of `simple` with unaccent
token mapping), generated stored column
`document_chunks.search_vector = to_tsvector('public.vietnamese_simple', content_raw)`
and GIN index `ix_document_chunks_search_vector`.

The earlier manual `upgrade_fts_vi.sql` exploration script is superseded; it
was moved to `scripts/archive_fts_experiments/` together with the other
one-off probes. No `search_vector_vi` A/B column exists — a single canonical
column serves all consumers.

## 5. Verification results (2026-08-27, rebuilt environment)

Environment note: the previous `.venv` was orphaned by removal of its base
interpreter (Python 3.14.6 Anaconda) and could not import `_ssl`; it was
rebuilt from `uv.lock` (`uv sync --frozen --extra dev`). All claims below are
reproducible today.

```text
uv run --frozen -m pytest tests/unit -q        # full unit suite … exit 0 (all green)
uv run --frozen -m pytest tests/unit/services/test_retrieval_foundation.py
                                               # 16 passed (models, SQL shape,
                                               # owner scope presence, UUID/
                                               # quote hardening, RRF math,
                                               # limit-after-fusion, concurrency)
uv run --frozen ruff check app tests alembic   # All checks passed!
uv run --frozen pyright <retrieval scope>      # 0 errors, 0 warnings
```

Live PostgreSQL integration of migrations 0007/0008 could not be executed in
this session (no Docker CLI available here); both revisions follow the same
dialect-guarded pattern as migrations 0005/0006 and are covered by the SQLite
chain-continuity test (`tests/unit/infrastructure/test_migrations.py` asserts
head == "0008"). Manual live verification remains recommended before P10D
benchmarking.

## 6. Security posture

Dynamic values validated before interpolation: ids parsed through
`uuid.UUID` (ValueError before any SQL runs), vectors rendered from Python
floats only, FTS user text escaped as one literal consumed by
`websearch_to_tsquery` (tested with input containing a single quote).
Retrievers never open their own connections or hold credentials — the default
provider uses the shared settings-driven engine, and the `RowProvider`
injection point keeps tests hermetic. Zero LLM calls; no future-phase code
landed ahead of its gate.

Known limitation (accepted): manual escaping relies on
`standard_conforming_strings = on` (PostgreSQL ≥ 9.1 default); migrating to
bound parameters is deferred (see §10).

## 7. Definition of Done (spec P10A)

- [x] `docs/architecture/rag-retrieval-strategy.md` created (all MUST items)
- [x] Retrieval modes defined; CORPUS/DOCUMENT/COMPARE semantics encoded
- [x] METADATA_LOOKUP cannot reach the retrieval path (contract guard)
- [x] RetrievalQuery implemented with owner scoping inputs + budget bounds
- [x] Dense child retrieval functional (pgvector, contract-validated embeddings)
- [x] Sparse child retrieval functional (PG FTS, vietnamese_simple)
- [x] Hybrid fusion (RRF k=60) running in parallel (concurrency test)
- [x] Candidate shape carries citation anchors (title/chunk_index/pages/label)
- [x] FTS migration 0007 + ANN index migration 0008
- [x] Unit tests added & full suite green; ruff/pyright clean
- [x] No LLM calls in the retrieval layer

## 8. Gate status

```text
GATE STATUS: APPROVED — P10A (user gate message received 2026-08-27)
Phase state: CLOSED. Next gate expected: APPROVED P10B   (then APPROVED P10C / APPROVED P10D)
```

## 9. Post-review fixes applied during the independent review session

Traceable to the review findings raised on 2026-08-27:

1. **(M1)** Migration `0008_add_hnsw_embedding_index.py`: HNSW cosine index on
   `document_chunks.embedding` — removes per-query sequential scans; memories
   intentionally excluded until their consumer phase justifies it.
2. **(M2)** `ANCHOR_SELECT_COLUMNS` + `chunk_anchor_metadata()`: candidates now
   carry citation-ready anchors (`document_title`, `chunk_index`,
   `page_start/page_end`, `citation_label`) populated by dense & sparse.
3. **(M4)** `RetrievalQuery` rejects `METADATA_LOOKUP` with a descriptive
   ValueError so metadata questions never silently run through embeddings.
4. **(L1)** `alembic/env.py`: autogenerate `include_object` filter excludes the
   runtime-generated `search_vector` column, preventing spurious
   `drop_column` diffs without mirroring a PG-only computed column into ORM
   models used against SQLite.
5. Repo hygiene (M3): root debug/experiment scripts archived under
   `scripts/archive_fts_experiments/`; disposable outputs removed; work
   committed to git in grouped commits.
6. Process reconciliation (H1/H2): this rewritten pack and the updated
   `plan/CURRENT_PHASE.md` gate ledger.

## 10. Deferred items (deliberate, not defects)

- bound-parameter migration for retrieval SQL (L3) — later hardening;
- single render of the query vector literal instead of SELECT+ORDER BY reuse (L2);
- explicit "not implemented yet" validation messaging for
  `require_source_diversity` / `expansion_policy` fields (L4) — behaviour
  lands in P10B as planned;
- pin a `ts_rank_cd` normalization variant before P10D ablations (L5);
- ruff import-order fix applied to migration 0007 while widening lint scope.

---
*Review Pack v2 — 2026-08-27*
