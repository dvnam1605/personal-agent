# RAG Retrieval Strategy (P10)

> Status: P10A **APPROVED & CLOSED** (2026-08-27). Sections previously marked
> **(P10B/C/D)** are binding decisions; those stamped **(P10B ✅)** were
> implemented on 2026-08-27 and are awaiting the user review gate.
> Source of truth: `plan/phases/P10_retrieval_rag_engine_execution_specification.md`.

## 1. Searchable unit

- Primary search unit = **CHILD** and **TABLE_CHILD** chunks
  (`document_chunks.hierarchy_level = 1`).
- PARENT rows (`hierarchy_level = 0`) are never direct retrieval targets;
  they are context payloads reached via expansion.

## 2. Ownership scoping (P9D-M3, mandatory)

Every candidate query filters:

```sql
d.is_active AND (documents.user_id = :requester OR documents.user_id IS NULL)
```

`requester_id IS NULL` sees only shared (`NULL`-owner) documents.

## 3. Hybrid search

Dense and sparse run **concurrently** (`asyncio.gather`) and must stay
independent: either leg may return empty without failing the other.

- Dense: pgvector cosine `<=>` over `document_chunks.embedding`
  (L2-normalized CLS embeddings from P9D — cosine distance ordering equals
  similarity ordering). Starting budget `top_k_dense ∈ [20, 40]` → default 20.
- Sparse: PostgreSQL FTS on generated column `search_vector`
  (`to_tsvector('public.vietnamese_simple', content_raw)`, unaccent mapping,
  GIN-indexed; migration 0007). Query text enters through
  `websearch_to_tsquery` so user punctuation cannot break tsquery syntax.
  Starting budget `top_k_sparse ∈ [20, 40]` → default 20.

## 4. Fusion

Reciprocal Rank Fusion baseline:

```text
fusion_score(chunk) = Σ_over_sources 1 / (k + rank_source),  k = 60
```

- Ranks are 1-based per source list.
- Only present sources contribute (a chunk found by one leg keeps a lower
  but non-zero score).
- Track per chunk: `dense_rank`, `sparse_rank`, `fusion_score`,
  `retrieval_sources`.
- No ad-hoc cross-score normalization (spec P10-05).

## 5. Reranking **(P10B ✅)**

Pluggable `Reranker` protocol over fused candidates (`async def rerank(query,
candidates, top_k)`); provenance tracked on every chunk via
`rerank_model` / `rerank_score` / `rerank_rank`. V1 ships an identity
(no-op) adapter so the pipeline is complete before any cross-encoder lands.
Model options pinned by ADR 0012.

## 6. Context expansion policy **(P10B ✅)**

`ExpansionPolicy ∈ {NONE, NEIGHBORS, PARENT}` carried on `RetrievalQuery`.

- NONE: fused CHILDs only.
- NEIGHBORS: sibling children around each hit within the same parent,
  bounded by token budget.
- PARENT: resolve full parent raw_text per top hit (dedup by parent id).

Deterministic resolution (no LLM): explicit query override wins; question
hints map to PARENT; everything else defaults to NONE (NEIGHBORS reachable
via override only). Hint matching folds case and strips diacritics, mirroring
the FTS unaccent posture. TABLE_CHILD hits are never swapped for their
parent — they stay standalone TABLE_CHILD evidence carrying heading-path
caption context instead.

## 7. Context packing **(P10B ✅)**

Greedy fill under a caller-supplied token budget using the P9C estimator
(`estimate_tokens`); hard max respected byte-exact; overflow items dropped
with a warning rather than truncated mid-chunk. Output is the normative
`EvidenceBundle` shape (items / total_tokens / documents_used /
parent_ids_used / retrieval_trace_id).

## 8. Compare-document diversity **(P10C)**

COMPARE_DOCUMENTS enforces per-document candidate caps so every required
source receives candidate opportunity before fusion cuts.

## 9. Sufficiency / no-answer **(P10C)**

Three-state verdict: SUFFICIENT / PARTIAL / INSUFFICIENT derived from fused
score distribution + coverage thresholds; INSUFFICIENT yields an explicit
no-answer path instead of forced synthesis.

## 10. Retry limits **(P10C)**

Bounded retrieval retry: maximum **2** attempts total with reformulated
`search_query` between attempts; never loops silently.

## 11. Citation contract **(P10C)**

Every evidence item carries `chunk_id`, `document_id`, `parent_id`,
page anchors and source filename from chunk metadata — enough for the UI to
render a verifiable citation without re-running retrieval.

## 12. Evaluation & ablation **(P10D)**

Versioned benchmark dataset with expected results; mandatory ablations:
single-level vs parent-child, dense-only vs sparse-only vs hybrid vs
hybrid+reranker, NONE vs NEIGHBORS vs PARENT. Results land in the family
Review Pack.

## 13. Latency budget **(P10D tracing)**

Stage timings (embed, dense, sparse, fuse, rerank, expand, pack) recorded on
every query via structured logs; end-to-end retrieval budget target ≤ 800 ms
p95 on CPU for V1 defaults (measured, not assumed — see P10D report).

## Implementation notes

- Retrieval services depend on a `RowProvider` abstraction; the default runs
  through the shared settings-driven SQLAlchemy engine — no hardcoded
  credentials anywhere.
- Dynamic SQL values are strictly validated before interpolation (UUIDs via
  `uuid.UUID`, floats rendered from Python numbers); FTS text is escaped as a
  single literal consumed by `websearch_to_tsquery`.
- Zero LLM calls in this layer ("no LLM when code is enough").
