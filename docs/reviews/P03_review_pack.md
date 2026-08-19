# P3 Review Pack — State Persistence, Redis, Observability & Budgets

## 1. Executive Summary
Phase 3 (P3) establishes the persistence layer, a durable database-backed audit outbox, privacy-sanitized append-only audit projections, environment-namespaced Redis caching, LangSmith distributed tracing with correlation ID separation, and hard token/cost/call budget enforcement for the Personal Multi-Agent AI Assistant.

---

## 2. Implemented Architecture & Invariant Specifications

### 2.1. PostgreSQL Schema & Core Models (`app/infrastructure/db/models.py`)
- Created the 14 required core models plus the P3-owned `audit_outbox` support model, with UUIDv4 primary keys and UTC-aware `TIMESTAMPTZ` timestamps:
  1. `users`: User identity and account state with case-normalized email uniqueness.
  2. `assistant_runs`: Complete execution run lifecycle, triage route, iteration counts, latency, and budget summary. Features mandatory internal `correlation_id` and optional `langsmith_trace_id`.
  3. `conversations`: Conversation sessions linking messages.
  4. `messages`: User, assistant, system messages with run_id linkage.
  5. `entities`: Resolved entity repository.
  6. `memories`: Memory store with vector embeddings, embedding model, and dimension metadata.
  7. `documents`: Ingested document metadata and provenance.
    8. `document_chunks`: P9-compatible parent/child chunks with source-block/page provenance, content hashes, chunker versions, raw and embedding content, and citation labels.
  9. `tool_executions`: Append-only audit trail for tool invocations.
  10. `llm_executions`: Append-only audit trail for LLM usage (never stores hidden reasoning/internal thoughts).
    11. `approval_requests`: P18-compatible mutation approval records with target, important arguments, expiry, status, and proposal hash fields.
  12. `audit_events`: Security and operational audit log.
  13. `skills`: Registered dynamic skill definitions with P14-aligned versioning.
    14. `workflow_runs`: Workflow execution instances.
    15. `audit_outbox`: Durable, sanitized events awaiting retryable materialization into append-only audit projections.

- `assistant_runs` enforces valid status values and terminal `completed_at` semantics, uses optimistic concurrency (`state_version`), and stores sanitized intermediate pause checkpoints.

### 2.2. Async Migrations (`alembic/`)
- `0001_initial_schema.py` creates the initial 14 core tables, indexes, and `pgvector` extension on PostgreSQL.
- `0002_p3_hardening.py` adds the durable audit outbox, checkpoint concurrency/status constraints, and forward-compatible P9/P18 schema fields without rewriting an applied migration.

### 2.3. Environment-Namespaced Redis Client & Graceful Degradation (`app/infrastructure/redis/client.py`)
- Standardized keyspaces:
  - `assistant:{env}:session:{session_id}` (TTL: 24h, versioned JSON envelope `{"version": 1, "data": ...}`)
  - `assistant:{env}:cache:{key}` (TTL: 1h, JSON)
  - `assistant:{env}:ratelimit:{user_id}:{bucket}` (TTL: 2m)
- Health check and graceful degradation:
  - Caches fail open.
  - Session state durable fallback to PostgreSQL.

### 2.4. Universal Privacy Redaction Engine & Durable Audit Outbox (`app/services/audit.py`)
- Redaction policy:
  - Secret keys matching `(?i)(token|secret|password|api_key|authorization|cookie|session|bearer)` masked with `[REDACTED_SECRET]`.
  - Email addresses masked as `u***@domain.com`.
  - Long strings truncated to `max_string_len=500` with `... [TRUNCATED]`.
  - Hidden chain-of-thought keys (`thought`, `reasoning`, `hidden_state`, `chain_of_thought`, `raw_thinking`) stripped.
  - Credential-like values embedded in strings are redacted and state snapshots use an explicit allowlist, dropping raw requests, tool results, prompts, and unbounded evidence.
- `AuditOutboxService`: persists the sanitized audit event in PostgreSQL first, then materializes it into `tool_executions`, `llm_executions`, or `audit_events` using retryable savepoints. Pending events keep the run's telemetry marked as degraded instead of being lost on process restart.

### 2.5. Run Persistence Service (`app/services/run_persistence.py`)
- Manages complete lifecycle: `create_run()`, `update_run_state()`, and `complete_run()`, linking `correlation_id` across DB records, tool executions, and LLM telemetry.
- Enforces legal lifecycle transitions and commits a sanitized checkpoint on every in-progress update, including `waiting_input` and `waiting_approval`; terminal states can only be persisted through `complete_run()`.

### 2.6. Atomic Concurrency-Safe Budget Manager (`app/services/budget_manager.py`)
- Per-run `asyncio.Lock` ensures race-condition-free reservations for future parallel DAG executions (P16).
- Atomic reservation methods (`reserve_llm_call`, `reserve_tool_call`) enforce call, prompt-token, completion-token, total-token, and estimated-cost limits before execution.
- A distributed reservation protocol is backed by Redis Lua compare-and-increment counters. It does not refill and fails closed if the shared store is unavailable.
- Watchdog verifies deadlines (`check_deadline()`) and raises `AppTimeoutError` or `BudgetExceededError`.
- Token and estimated USD cost accumulation ($0.000001 precision).

### 2.7. LangSmith Distributed Tracing (`app/infrastructure/observability/tracing.py`)
- Context manager `trace_span()` tracks span hierarchy, latency, and captured exceptions.
- Zero network overhead when `tracing_enabled=False` (no-op context manager).
- Guaranteed failure isolation: tracing exporter failures never disrupt user requests.

---

## 3. Verification & Quality Gates

### 3.1. Automated Test Suite
- **118 automated tests passing, including the live PostgreSQL migration path**:
  - `tests/unit/infrastructure/test_db_models.py` (All 14 ORM models & relationships)
  - `tests/unit/infrastructure/test_migrations.py` (Schema completeness and disposable `alembic upgrade head` verification)
  - `tests/unit/infrastructure/test_redis.py` (Environment keyspaces, TTLs, and graceful degradation)
  - `tests/unit/infrastructure/test_tracing.py` (LangSmith spans, disabled no-op, failure isolation)
  - `tests/unit/services/test_redaction.py` (Secret masking, email masking, chain-of-thought stripping, snapshot sanitization)
  - `tests/unit/services/test_run_persistence.py` (Full run lifecycle, durable audit delivery, checkpoint transitions, and audit linkage)
  - `tests/unit/services/test_budget_manager.py` (Atomic reservations, concurrent multi-worker safety, timeout deadline, token/cost reservations, and distributed fail-closed handling)
  - `tests/unit/services/test_retention.py` (Retention purge ordering and protection of runs with pending audit delivery)
  - `tests/integration/test_postgres_persistence.py` (Disposable-schema `alembic upgrade head`, pgvector, FK/index checks, and vector-query integration)

### 3.2. Codebase Health & Coverage
- **Total Codebase Coverage**: 94% across all modules.
- **Ruff**: Passed with 0 errors, 0 warnings.
- **Pyright**: Passed with 0 errors, 0 warnings, 0 informations.
