# P10B Review Pack: Processing Pipeline & Expansion Policies

> Spec scope: P10-06..P10-13. Gate authorised by user message
> `"ok approved p10b"` (2026-08-27); implemented + self-reviewed + verified
> green the same day. This pack is generated against the exact working tree.

## 1. Summary

P10B turns the P10A fused candidates into a budget-aware `EvidenceBundle`
through a fully deterministic stage chain (no LLM anywhere):

```text
HybridRetrievalService (P10A)
  -> apply_diversity            dedup ids -> near-dup suppression -> caps      (P10-06)
  -> Reranker.rerank            pluggable seam + IdentityReranker v1          (P10-07)
  -> resolve_expansion_policy   deterministic NONE / PARENT (+ override)       (P10-08)
  -> ExpansionService           parent resolver/scoring | neighbor windows    (P10-09..12)
  -> build_bundle               greedy packing under hard token budget        (P10-13)
```

## 2. Deliverables

New files:

```text
app/services/retrieval/diversity.py     # P10-06 chain + compare balance cap helper
app/services/retrieval/rerank.py        # Reranker protocol + IdentityReranker
app/services/retrieval/expansion.py     # P10-08..12 policy + parent/neighbor builders
app/services/retrieval/packing.py       # Evidence/EvidenceBundle assembly (P10-13)
app/services/retrieval/pipeline.py      # RetrievalPipeline orchestrator + HybridRetriever protocol
tests/unit/services/test_retrieval_pipeline.py   # 24 unit tests
```

Modified:

```text
app/domain/models/retrieval.py          # RetrievedChunk rerank provenance fields;
                                        # context_token_budget; Evidence; EvidenceBundle
app/services/retrieval/sql.py           # ANCHOR_SELECT_COLUMNS += node_type/heading_path;
                                        # PARENT_FETCH_TEMPLATE; SIBLING_FETCH_TEMPLATE;
                                        # parent_scope_sql/sibling_scope_sql; heading coercion
app/services/retrieval/__init__.py      # public surface extended
docs/architecture/rag-retrieval-strategy.md  # §5–§7 stamped delivered with behaviour notes
```

## 3. Spec traceability

| Task | Decision implemented | Where |
|---|---|---|
| P10-06 | dedup by chunk_id; normalised-body near-duplicate suppression (case/space folded, first-wins); per-document caps in arrival order; COMPARE mode derives a balance cap `ceil(n/docs)` — **missing-source detection deferred to P10C** | `diversity.py` |
| P10-07 | `Reranker` protocol mirrors spec signature; returns provenance (`rerank_model/rerank_score/rerank_rank`); `IdentityReranker` passthrough keeps fusion order, tags `identity:v1`; ViRanker adapter pinned by ADR 0012 lands behind the seam later | `rerank.py` |
| P10-08 | explicit override wins → question-intent hints (EN+VN, case/diacritic-folded) → PARENT; default NONE; NEIGHBORS reachable only via override | `expansion.py::resolve_expansion_policy` |
| P10-09 | children grouped by `parent_id`, ONE Evidence per parent; missing payloads warn-and-skip (never fabricate) | `_parent_units` |
| P10-10 | documented baseline: `priority = best_child_score + 0.1 * min(extra_hits, 5)`; constant recorded for P10D ablation tuning | `_priority` |
| P10-11 | prev/hit/next windows strictly within same parent; multiple hits merge into a single ordered group so overlap text never repeats | `_neighbor_units` |
| P10-12 | TABLE_CHILD candidates excluded from parent swaps; emitted as standalone `TABLE_CHILD` units carrying heading-path caption context (chunker already repeats table headers in raw rows) | `build_units` / `unit_for_chunk` |
| P10-13 | normative `EvidenceBundle(items,total_tokens,documents_used,parent_ids_used,retrieval_trace_id)`; mixed kinds CHILD/NEIGHBOR_GROUP/PARENT/TABLE_CHILD; greedy fill via P9C `estimate_tokens`; overflow = drop-with-warning, byte-exact hard max | `packing.py` |

## 4. Security & scoping posture

- every re-fetch (parents, siblings) re-applies the mandatory filters:
  `d.is_active AND (user_id = requester OR user_id IS NULL)` (P9D-M3) —
  asserted in tests;
- IN-lists built through `uuid_literal`, so malformed ids raise before SQL;
- MAX_EXPANSION_PARENTS=32 bounds fetch fan-out; rerank input bounded by
  `rerank_top_k_max=48` (spec range ~20–50).

## 5. Verification (2026-08-28 — Post-Review Fixes v2)

```text
uv run --frozen --no-sync -m pytest tests/unit -q               # full suite 7912 lines, exit 0
uv run --frozen --no-sync -m pytest tests/unit/services/test_retrieval_pipeline.py   # 32 passed
uv run --frozen --no-sync ruff check app tests alembic          # All checks passed!
uv run --frozen --no-sync pyright app/services/retrieval \
        app/domain/models/retrieval.py alembic/env.py \
        tests/unit/services/test_retrieval_pipeline.py          # 0 errors, 0 warnings
```

Coverage notes: dedup/near-dup/caps/balance-cap, identity provenance (`pre_rerank_rank`, `rerank_rank`, `rerank_score`, `rerank_model`),
override-vs-hint decision matrix (incl. unaccented VN question), single-parent
grouping + scoring order based on `rerank_score`, TABLE_CHILD interleaving and priority (not appended last),
owner-guard presence on both fetch templates, syntax validity (`{parent_scope}`, `{sibling_scope}` without double-IN),
window ±1 & multi-hit merge & cross-parent separation, neighbor lead selection pinning to hit chunk,
greedy budget math, oversize-drop-not-truncate, `parent_ids_used` scoped strictly to expanded units,
bundle fields/trace format, end-to-end NONE flow, and COMPARE-mode missing source diagnostics.

## 6. Definition of Done (P10B scope)

- [x] duplicate child IDs removed before rerank
- [x] near-duplicate/adjacent-overlap suppression (exact-normalised match; tunable recorded)
- [x] per-document candidate caps; compare-mode opportunity cap
- [x] pluggable reranker interface + tracked provenance (`pre_rerank_rank`, `rerank_rank`, `rerank_score`, `rerank_model`) + identity adapter
- [x] deterministic expansion decision rules (no LLM) with override path
- [x] parent grouping without duplicates; documented priority formula using `rerank_score`
- [x] neighbor boundaries confined to same parent; overlap-free merging; lead chunk points to hit chunk
- [x] table-child behaviour: headers retained, caption context, no parent swap, proper ranking interleaving
- [x] context budget enforced exactly (greedy, warn-drop, never truncate)
- [x] `EvidenceBundle` matches the spec field-for-field with clean `parent_ids_used` semantics
- [x] unit tests for every rule above + e2e flow; full suite/ruff/pyright green

## 7. Post-Review Findings Resolution (2026-08-28)

| Item | Description | Resolution |
|---|---|---|
| **H1** | SQL fetch parents/siblings syntax error (`c.id IN (c.id IN ('...'))`) | Changed `PARENT_FETCH_TEMPLATE` & `SIBLING_FETCH_TEMPLATE` placeholders to `{parent_scope}` and `{sibling_scope}` to match `parent_scope_sql` / `sibling_scope_sql`. Added SQL syntax non-nesting unit assertions. |
| **H2** | Parent priority used raw fusion score instead of `rerank_score` | `ExpansionService` now reads `chunk.rerank_score` (fallback to `chunk.score`) for parent group priority scoring. Added test asserting rerank score overrides raw fusion score. |
| **H3** | TABLE_CHILD appended at the end breaking priority & budget | `build_units` now maintains ranking interleaving and priority score ordering across both table children and expanded units. Added test asserting high-scoring table child is packed first. |
| **H4** | Neighbor lead chunk selection took arbitrary index member | Lead chunk is now selected as the best hit chunk in the parent group; `primary_chunk_id`, `document_id`, `heading_path`, and `anchors` match the genuine search match. Added anchor validation test. |
| **M1** | Near-duplicate suppression exact matching limitation | Documented V1 exact normalised text matching and noted Jaccard/shingle threshold as tunable for P10D ablations. |
| **M2** | NEIGHBORS expansion routing documentation | Documented agent strategy ownership and override mechanism for NEIGHBORS. |
| **M3** | Hybrid retrieval sliced to `query.limit` before diversity/rerank | `HybridRetrievalService.retrieve` now returns full fused candidate set for pipeline diversity and rerank consumption (~20-40 pool). |
| **M4** | `parent_ids_used` collected parent ids for unexpanded `CHILD` units | `build_bundle` now scopes `parent_ids_used` strictly to units with `kind in ("PARENT", "NEIGHBOR_GROUP")`. |
| **M5** | Rerank provenance missing `pre_rerank_rank` | Added `pre_rerank_rank: int | None = None` to `RetrievedChunk` and populated in `IdentityReranker.rerank`. |
| **L1** | `suppress_near_duplicates` kept empty chunks | Empty / whitespace-only chunks are now filtered out. |
| **L2** | Diagnostic warning for missing compare documents | `apply_diversity` now logs a warning when candidate pool lacks a requested compare document. |
| **L3** | Logging `expansion_built` missing `retrieval_trace_id` | `trace_id` is passed down to `ExpansionService.build_units` and included in debug logs. |
| **L4** | Token budget drop monitoring | Budget overflow logs and tests verified. |

## 7.1 Residual Notes & P10C / P10D Handoffs

- **M-RES1 (`RetrievalQuery.limit` contract handoff to P10C)**:
  `RetrievalQuery.limit` (default 10) was originally sliced at `HybridRetrievalService`. In P10B, `HybridRetrievalService` returns the full candidate set (~20–40) to feed diversity and rerank, while budget packing enforces `context_token_budget`. In P10C (Answer Synthesis / Policies), `query.limit` will be wired into the rerank top-k selection / max evidence items cap or formally documented as an item-count ceiling.
- **L1 (Cross-kind scoring & parent bonus ablation in P10D)**:
  `ExpansionService` combines `TABLE_CHILD` score (0..1) with `PARENT` priority score (`best_child_score + 0.1 * min(extras, 5)`). This baseline follows spec P10-10; P10D benchmark ablations will specifically evaluate tuning the `0.1` bonus and 5-hit cap against high-precision table hits.
- **L2 (Owner-scope DB integration tests in P10D)**:
  Unit tests assert the mandatory SQL predicates (`d.user_id = :requester OR d.user_id IS NULL`), with `owner_scope_sql(None)` returning `d.user_id IS NULL` (shared docs only). Full live-PG matrix integration testing across both requester-authenticated and unauthenticated contexts will run under P10D evaluation.

## 8. Gate status

```text
GATE STATUS: APPROVED & CLOSED — P10B (user "approved p10B" 2026-08-28)
Next gate: APPROVED P10C → APPROVED P10D
```

---
*Review Pack updated 2026-08-28 (v3 — approved & closed)*
