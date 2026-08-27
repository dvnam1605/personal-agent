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

## 5. Verification (2026-08-27)

```text
uv run --frozen -m pytest tests/unit -q                # full suite … exit 0
uv run --frozen -m pytest tests/unit/services/test_retrieval_pipeline.py   # 24 passed
uv run --frozen ruff check app tests alembic           # All checks passed!
uv run --frozen pyright app\services\retrieval \
        app\domain\models\retrieval.py alembic\env.py \
        tests\unit\services\test_retrieval_pipeline.py  # 0 errors
```

Coverage notes: dedup/near-dup/caps/balance-cap, identity provenance,
override-vs-hint decision matrix (incl. unaccented VN question), single-parent
grouping + scoring order, TABLE_CHILD isolation (asserts zero DB calls),
owner-guard presence on both fetch templates, window ±1 & multi-hit merge &
cross-parent separation, greedy budget math, oversize-drop-not-truncate,
bundle fields/trace format, end-to-end NONE flow and COMPARE-mode cap.

## 6. Definition of Done (P10B scope)

- [x] duplicate child IDs removed before rerank
- [x] near-duplicate/adjacent-overlap suppression (documented normalisation)
- [x] per-document candidate caps; compare-mode opportunity cap
- [x] pluggable reranker interface + tracked provenance + identity adapter
- [x] deterministic expansion decision rules (no LLM) with override path
- [x] parent grouping without duplicates; documented priority formula
- [x] neighbor boundaries confined to same parent; overlap-free merging
- [x] table-child behaviour: headers retained, caption context, no parent swap
- [x] context budget enforced exactly (greedy, warn-drop, never truncate)
- [x] `EvidenceBundle` matches the spec field-for-field
- [x] unit tests for every rule above + e2e flow; full suite/ruff/pyright green

## 7. Design notes & deferred items

- Parent scoring constants (`0.1` bonus, 5-hit cap), hint list, and
  `MAX_EXPANSION_PARENTS` are recorded here as the tunables P10D ablations
  must vary consciously rather than rediscover.
- Oversized single units are dropped whole by design (strategy §7); if
  benchmarks show starvation on long parents, a member-level splitting pass
  can be added without changing the Evidence contract.
- Compare-source *missing detection/reporting* is explicitly P10C territory;
  only the opportunity cap landed in P10B.
- Sibling windows fetch all children of affected parents at level 1 — bounded
  by MAX_EXPANSION_PARENTS; fine at personal corpus scale, revisit if needed.

## 8. Gate status

```text
GATE STATUS: WAITING FOR USER REVIEW — P10B
Next gate message expected: APPROVED P10B   (then APPROVED P10C / APPROVED P10D)
```

---
*Review Pack generated 2026-08-27*
