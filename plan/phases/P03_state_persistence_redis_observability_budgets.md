> Active phase file for P3. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P3`.

# P3 — STATE PERSISTENCE + REDIS + OBSERVABILITY + BUDGETS

## 1. Objective

Make every execution run end-to-end traceable, measurable, privacy-sanitized, and budget-governed with durable persistence before connecting user data or external LLM providers.

---

## 2. Core Architectural Specifications

### 2.1. PostgreSQL Persistence & 14 Canonical Tables (P3-01, P3-02, P3-05)

#### Universal Conventions
- **Primary Keys**: UUIDv4 strings (`String(36)`).
- **Timestamps**: UTC timezone-aware timestamps (`DateTime(timezone=True)` / `TIMESTAMPTZ`), defaulting to `default=lambda: datetime.now(UTC)`.
- **JSON Fields**: JSON / JSONB for structured payloads.
- **Cost Precision**: USD currency with 6 decimal places (`Numeric(10, 6)`), defaulting to `$0.000000`.
- **Email Uniqueness**: Case-normalized emails (`email.lower().strip()`).

#### Table Schemas & Relationships

1. **`users`**
   - `id`: `String(36)` PK (UUIDv4)
   - `email`: `String(255)` UNIQUE NOT NULL (Case-normalized)
   - `full_name`: `String(255)` NULLABLE
   - `is_active`: `Boolean` DEFAULT TRUE NOT NULL
   - `created_at`: `TIMESTAMPTZ` NOT NULL
   - `updated_at`: `TIMESTAMPTZ` NOT NULL
   - *Indexes*: `ix_users_email` (unique)

2. **`assistant_runs`** (Mapping from `AssistantState` & `RunTelemetry`)
   - `id`: `String(36)` PK (UUIDv4 `run_id`, idempotency key)
   - `session_id`: `String(36)` FK -> `conversations.id` (ON DELETE SET NULL), NULLABLE
   - `user_id`: `String(36)` FK -> `users.id` NOT NULL
   - `correlation_id`: `String(64)` NOT NULL (Mandatory internal application trace ID)
   - `langsmith_trace_id`: `String(64)` NULLABLE (Nullable vendor trace ID)
   - `langsmith_run_id`: `String(64)` NULLABLE
   - `request`: `Text` NOT NULL (Sanitized raw input prompt)
   - `normalized_request`: `Text` NULLABLE
   - `goal`: `Text` NULLABLE
   - `route_type`: `String(32)` NOT NULL (`direct_specialist`, `known_workflow`, `supervisor`, `casual_response`)
   - `domains`: `JSONB` NOT NULL (List of domain strings)
   - `complexity`: `String(32)` NOT NULL (`direct`, `adaptive`, `multi_step`, `open_supervised`)
   - `workflow_name`: `String(64)` NULLABLE
   - `active_skill`: `String(64)` NULLABLE
   - `status`: `String(32)` NOT NULL (`pending`, `running`, `waiting_input`, `waiting_approval`, `completed`, `failed`, `cancelled`)
   - `iteration`: `Integer` DEFAULT 0 NOT NULL
   - `react_steps`: `Integer` DEFAULT 0 NOT NULL
   - `tool_call_count`: `Integer` DEFAULT 0 NOT NULL
   - `llm_call_count`: `Integer` DEFAULT 0 NOT NULL
   - `prompt_tokens`: `Integer` DEFAULT 0 NOT NULL
   - `completion_tokens`: `Integer` DEFAULT 0 NOT NULL
   - `total_tokens`: `Integer` DEFAULT 0 NOT NULL
   - `estimated_cost_usd`: `Numeric(10, 6)` DEFAULT 0.000000 NOT NULL
   - `parallel_time_ms`: `Float` DEFAULT 0.0 NOT NULL
   - `total_latency_ms`: `Float` DEFAULT 0.0 NOT NULL
   - `state_snapshot`: `JSONB` NULLABLE (Explicitly sanitized allowlisted state snapshot)
   - `error_summary`: `Text` NULLABLE (Sanitized)
   - `telemetry_degraded`: `Boolean` DEFAULT FALSE NOT NULL (Flag indicating if audit outbox buffering occurred)
   - `created_at`: `TIMESTAMPTZ` NOT NULL
   - `updated_at`: `TIMESTAMPTZ` NOT NULL
   - `completed_at`: `TIMESTAMPTZ` NULLABLE
   - *Indexes*: `ix_runs_user_created (user_id, created_at DESC)`, `ix_runs_session (session_id)`, `ix_runs_correlation (correlation_id)`

3. **`conversations`** & **`messages`**
   - `conversations`: `id` PK, `user_id` FK -> `users.id`, `title`, `metadata` (JSONB), `created_at`, `updated_at`
   - `messages`: `id` PK, `conversation_id` FK -> `conversations.id`, `run_id` FK -> `assistant_runs.id` (NULLABLE), `role` (`user`, `assistant`, `system`, `tool`), `content` (Sanitized Text), `metadata` (JSONB), `created_at`
   - *Indexes*: `ix_messages_conv_created (conversation_id, created_at ASC)`

4. **`tool_executions`** (Append-only Audit Log)
   - `id`: `String(36)` PK (UUIDv4)
   - `run_id`: `String(36)` FK -> `assistant_runs.id` NOT NULL
   - `agent_name`: `String(64)` NULLABLE
   - `tool_name`: `String(64)` NOT NULL
   - `input_parameters`: `JSONB` NOT NULL (Sanitized / redacted)
   - `output_summary`: `JSONB` NULLABLE (Sanitized / truncated)
   - `success`: `Boolean` NOT NULL
   - `error_message`: `Text` NULLABLE
   - `latency_ms`: `Float` NOT NULL
   - `cached`: `Boolean` DEFAULT FALSE NOT NULL
   - `retry_count`: `Integer` DEFAULT 0 NOT NULL
   - `executed_at`: `TIMESTAMPTZ` NOT NULL
   - *Indexes*: `ix_tool_exec_run (run_id)`, `ix_tool_exec_name_time (tool_name, executed_at DESC)`

5. **`llm_executions`** (Append-only Audit Log)
   - `id`: `String(36)` PK (UUIDv4)
   - `run_id`: `String(36)` FK -> `assistant_runs.id` NOT NULL
   - `agent_name`: `String(64)` NULLABLE
   - `provider`: `String(32)` NOT NULL (e.g. `google`, `openai`, `anthropic`)
   - `model`: `String(64)` NOT NULL (e.g. `gemini-1.5-pro`, `gpt-4o`)
   - `purpose`: `String(64)` NOT NULL (e.g. `triage`, `agent_execution`, `synthesis`)
   - `prompt_tokens`: `Integer` DEFAULT 0 NOT NULL
   - `completion_tokens`: `Integer` DEFAULT 0 NOT NULL
   - `total_tokens`: `Integer` DEFAULT 0 NOT NULL
   - `estimated_cost_usd`: `Numeric(10, 6)` DEFAULT 0.000000 NOT NULL
   - `latency_ms`: `Float` NOT NULL
   - `success`: `Boolean` NOT NULL
   - `error_message`: `Text` NULLABLE
   - `executed_at`: `TIMESTAMPTZ` NOT NULL
   - *Security Rule*: Chain-of-thought, hidden reasoning, and internal prompt templates are never persisted.
   - *Indexes*: `ix_llm_exec_run (run_id)`, `ix_llm_exec_model (provider, model)`

6. **`audit_events`** (Security & Policy Audit Trail)
   - `id`: `String(36)` PK (UUIDv4)
   - `run_id`: `String(36)` FK -> `assistant_runs.id` NULLABLE
   - `user_id`: `String(36)` FK -> `users.id` NULLABLE
   - `event_type`: `String(64)` NOT NULL (`security_gate`, `policy_violation`, `budget_breach`, `auth_event`)
   - `component`: `String(64)` NOT NULL
   - `severity`: `String(16)` NOT NULL (`INFO`, `WARNING`, `ERROR`, `CRITICAL`)
   - `payload`: `JSONB` NOT NULL (Sanitized event payload)
   - `created_at`: `TIMESTAMPTZ` NOT NULL
   - *Indexes*: `ix_audit_events_created (created_at DESC)`, `ix_audit_events_type (event_type)`

7. **Minimal Infrastructure Schemas for Future-Owned Tables (Aligned with P9, P14, P17, P18)**
   - `entities` (P17 Owner): `id`, `user_id` FK, `entity_type`, `canonical_name`, `aliases`, `attributes`, `confidence`, `created_at`, `updated_at`
   - `memories` (P17 Owner): `id`, `user_id` FK, `memory_type`, `content`, `embedding` (Vector 1536), `embedding_model`, `embedding_dimensions`, `importance`, `last_accessed_at`, `created_at`, `updated_at`
   - `documents` (P08/P09 Owner): `id`, `user_id` FK, `logical_document_id`, positive `version_number`, `source_type`, `external_id`, `title`, `uri`, `mime_type`, `source_content_hash`, bounded `metadata`, constrained `status`, `is_active`, `created_at`, `updated_at`; a partial unique index permits one active version per logical document.
   - `document_chunks` (P09/P10 Owner): `id`, `document_id` FK, `chunk_index`, `hierarchy_level`, `node_type`, non-null `heading_path` and `source_block_ids`, `content_raw`, `content_embedding_text`, `embedding` (Vector 1536), `embedding_model`, `embedding_dimensions`, `provenance_uri`, `citation_label`, bounded `metadata`, `created_at`
   - `approval_requests` (P18 Owner): `id`, `run_id` FK, `action_type`, `description`, `target`, bounded `important_arguments` and `parameters`, `tool_name`, `risk_level`, constrained lifecycle `status`, decision/status consistency, `expires_at`, `proposal_hash`, `approved`, `approver_id`, `reason`, `created_at`, `decided_at`; pending proposals are unique per run.
   - `skills` (P14 Owner): `id`, `name` UNIQUE, `version`, `category`, `description`, `definition_json`, `is_active`, `created_at`, `updated_at`
   - `workflow_runs` (P15/P16/P19 Owner): `id`, `run_id` FK, `workflow_name`, `workflow_version`, `status`, `executed_node_ids`, `outputs`, `error_details`, `created_at`, `completed_at`

---

### 2.2. pgvector & Multi-Model Embedding Specifications (P3-03)
- **Model Metadata**: Every embedding column (`memories.embedding`, `document_chunks.embedding`) is accompanied by `embedding_model` (e.g. `"text-embedding-3-large"`) and `embedding_dimensions` (`1536`) to prevent incompatible vector mixing across model migrations.
- **Distance Metric**: Cosine distance (`<=>`).
- **PostgreSQL Extension**: `CREATE EXTENSION IF NOT EXISTS vector;` executed in migration `0001_initial_schema.py`.

---

### 2.3. Redis Namespaced Keys & Degradation Behavior (P3-04)
- **Environment Namespacing**:
  - `assistant:{env}:session:{session_id}` -> TTL: 86400s (24h), Envelope: `{"version": 1, "data": ...}`
  - `assistant:{env}:cache:{key}` -> TTL: 3600s (1h)
  - `assistant:{env}:ratelimit:{user_id}:{bucket}` -> TTL: 120s (2m)
- **Degradation Policy by Data Type**:
  - **Ephemeral Caches**: Fail-open (returns `None`, falls back to database/backend query, logs structured warning).
  - **Durable Checkpoints & Sessions**: Backed by PostgreSQL (`conversations` and `assistant_runs.state_snapshot`). If Redis is unavailable, state reads/writes persist directly to PostgreSQL without data loss across restarts.
  - **Rate Limits**: Configurable fail-closed for sensitive mutation routes, fail-open for triage queries.

---

### 2.4. Run Lifecycle, Audit Outbox & Failure Handling (P3-05, P3-06, P3-07)
- **Run State Machine**:
  - `START`: Insert `assistant_runs` record (`status='running'`). If initial insert fails, run is aborted with canonical `DatabaseError`.
  - `IN_PROGRESS`: Tool and LLM audit events written.
  - `TERMINATION`: Update `assistant_runs` (`status='completed' | 'failed' | 'cancelled'`), write `completed_at`, total latency, token counters, and sanitized state snapshot.
- **Durable Audit Outbox**:
  - Audit writes are appended to the database-backed `audit_outbox` in the same transaction as the run.
  - A production `AuditOutboxWorker` claims rows with PostgreSQL `FOR UPDATE SKIP LOCKED`, retries failed rows with backoff, recovers abandoned claims, and commits projection plus delivery status atomically.
  - Projections carry a unique `outbox_id`, making retries idempotent and preventing duplicate audit rows.
  - `telemetry_degraded` remains true while a run has any pending, processing, or failed outbox row; delivery is not dependent on a request lifecycle callback.

---

### 2.5. Durable Waiting Checkpoints & Approval Lifecycle (P3-05, P3-07, P3-10)
- The run row retains the sanitized request fallback; waiting snapshots retain goal, route/task context, execution plan, task results, missing information, proposed actions, approvals, counters, errors, and continuation context needed after a process restart.
- `RunPersistenceService.load_state()` rehydrates a typed `AssistantState`; `resume_waiting_input()` records bounded user input and transitions the run back to `running`.
- `ApprovalRequestService` creates idempotent hashed proposals, validates immutable arguments, enforces `pending -> approved | rejected | expired | cancelled`, expires stale requests, and resumes approved requests into the waiting run; the scheduled maintenance worker invokes batch expiry operationally.
- Terminal run completion is idempotent for the same terminal status, terminal timestamps are not rewritten, and optional expected state versions reject stale writers.

### 2.6. Universal Privacy Redaction Policy (P3-06, P3-07)
- **Recursive Sanitizer**: Applied at **every** boundary: requests, messages, error summaries, state snapshots, metadata, approval arguments, and audit records.
- **OAuth & Secrets Redaction**: Keys matching `(?i)(token|secret|password|api_key|apikey|authorization|cookie|session|bearer|private_key)` replaced with `[REDACTED_SECRET]`.
- **Email & PII Masking**: Email addresses masked as `u***@domain.com`.
- **Payload Truncation**: Strings exceeding maximum lengths are safely truncated with `... [TRUNCATED]`.
- **Chain-of-Thought Stripping**: Internal keys `thought`, `reasoning`, `hidden_state`, `chain_of_thought`, `internal_monologue`, `raw_thinking` are strictly excluded from state snapshots and audit logs.
- **Structural Bounds**: Generic JSON payloads are capped by string length, nesting depth, collection item count, and aggregate serialized size; model validators cover message/document metadata and approval/audit JSON fields.

---

### 2.7. Atomic Concurrency-Safe Budget Enforcement (P3-09)
- **Scopes**: Run Scope (`ExecutionBudget`), Task Scope, Individual Call Scope.
- **Concurrency Model**:
  - In-process: Protected with per-run `asyncio.Lock` for race-condition-free reservations.
  - Multi-instance / worker DAGs (P16): Redis run-scoped non-refilling atomic reservation counters implemented by Lua with a fixed TTL.
  - Reserve-before-call: `reserve_llm_call()` and `reserve_tool_call()` check available headroom before allowing execution. Completion-only integrations must use `record_llm_metrics()`, which also reserves the distributed call, token, and cost counters.
- **Deadline Watchdog**:
  - Raises `AppTimeoutError` if `elapsed_seconds > timeout_seconds`.
- **Cost & Token Accounting**:
  - Tracks `prompt_tokens`, `completion_tokens`, `total_tokens`, and `estimated_cost_usd` ($0.000001 precision).

---

### 2.8. LangSmith Distributed Tracing & Correlation (P3-08)
- **Correlation Separation**:
  - `correlation_id`: Mandatory internal identifier linking application logs, `assistant_runs`, and audit entries.
  - `langsmith_trace_id`: Nullable vendor-specific trace ID, populated only when `tracing_enabled=True`.
- **Disabled Mode Guarantee**: When `tracing_enabled=False`, `trace_span()` acts as a zero-overhead no-op context manager with 0 outbound network requests.
- **Failure Isolation**: Tracing exporter network exceptions are caught and logged; tracing errors never fail an assistant execution turn.

---

### 2.9. Data Retention & Immutability Policy
- **Audit Immutability**: Tables `tool_executions`, `llm_executions`, and `audit_events` have no update APIs; only the authorized retention worker may delete expired rows.
- **Retention**: Default 90-day retention for telemetry and audit events, executed by the scheduled `RetentionWorker` with an explicit deletion authority.
- **Outbox protection**: Pending, processing, and failed rows protect their run from purge. Orphaned unresolved rows with `run_id IS NULL` are bounded by the same retention window.

---

## 3. P3 Verification Suite & Quality Gates

### 3.1. Test Matrix
- `tests/unit/infrastructure/test_db_models.py`: All 14 ORM models, relationships, and vector metadata.
- `tests/unit/infrastructure/test_migrations.py`: Schema completeness and table creation.
- `tests/unit/infrastructure/test_redis.py`: Keyspaces, TTLs, and graceful degradation.
- `tests/unit/infrastructure/test_tracing.py`: Spans, disabled no-op, and failure isolation.
- `tests/unit/services/test_redaction.py`: Secret masking, email masking, chain-of-thought stripping, truncation, and state snapshot sanitization.
- `tests/unit/services/test_run_persistence.py`: Run lifecycle, outbox buffering, and audit event linkage.
- `tests/unit/services/test_approvals.py`: Approval creation, hashing, expiry, idempotent decisions, and resume.
- `tests/unit/services/test_budget_manager.py`: Atomic reservations, concurrent safety, timeout deadlines, and cost accumulation.
- `tests/integration/test_postgres_persistence.py`: Live PostgreSQL + pgvector integration tests.
- Direct PostgreSQL tests cover outbox claims/projection uniqueness, document activation constraints, and a cosine-distance query.

### 3.2. Verification Criteria
- 100% test pass rate across unit test suite.
- Zero errors in `ruff check` and `pyright`.
- Code coverage >= 90% across domain, infrastructure, and services.

---

## 4. P3 Gate

STOP. Submit P3 Review Pack and await `APPROVED P3`.
