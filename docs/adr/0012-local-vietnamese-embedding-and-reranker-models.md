# ADR 0012 — Local Vietnamese Embedding + Reranker Models (1024 dims)

**Status:** APPROVED (user decision, 2026-08-22)
**Affected phases:** P3 (schema), P9 (ingestion/embedding), P10 (retrieval/rerank)

## Context

The P3 schema assumed hosted OpenAI embeddings (`text-embedding-3-large`,
1536 dims). The project now standardizes on offline, locally cached
Vietnamese models for privacy, cost, and language quality:

| Role | Model | Output |
|---|---|---|
| Embedding | `AITeamVN/Vietnamese_Embedding` | 1024-dim vectors |
| Reranker | `namdp-ptit/ViRanker` | cross-encoder relevance score |

Both snapshots are copied to `models/` (gitignored) in HuggingFace cache
layout and load with `local_files_only=True`.

## Decision

1. Resize `memories.embedding` and `document_chunks.embedding` from
   `vector(1536)` to `vector(1024)` (Alembic `0005`). Existing rows are nulled:
   the deployment is pre-production and embeddings are always rebuilt during
   ingestion.
2. Defaults: `EMBEDDING__MODEL=AITeamVN/Vietnamese_Embedding`,
   `EMBEDDING__DIMENSIONS=1024`, `RERANKER__MODEL=namdp-ptit/ViRanker`.
   Both expose optional `*_LOCAL_PATH` settings pointing at local snapshots.
3. Mixing embedding dimensions/models remains forbidden: every stored vector
   row records `embedding_model` + `embedding_dimensions`; a model change
   requires a full reindex migration.

## Consequences

- ~33% smaller vector storage per chunk versus 1536 dims.
- No per-call embedding cost; ingestion throughput bounded by local hardware.
- P9's `EmbeddingService` must implement the sentence-transformers/transformers
  path with dimension validation against the configured value (1024).
- Deviation from P3 spec text ("Vector 1536") is recorded here as an approved
  architecture change per MASTER_PLAN §29.

## Alternatives considered

- Keep 1536 via another hosted provider: rejected — recurring cost, data
  leaves machine, weaker Vietnamese quality.
- Store 1024 vectors inside a fixed 1536 column (zero-padding): rejected —
  wastes space and corrupts distance semantics.
