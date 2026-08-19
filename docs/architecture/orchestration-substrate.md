# Orchestration Substrate Architecture Contract

Governing decision: ADR 0011. Read alongside `execution-paths.md`, `loop-vs-graph.md`, and `agent-communication.md`.

## 1. Core Split

LangGraph provides the **substrate** — the mechanics of running, checkpointing, pausing, and resuming a graph. It does **not** provide the **policy** — the decisions about which agent runs, with which tools, under which budget, and when to stop.

```text
        ┌─────────────────────────────────────────────┐
        │  ORCHESTRATION POLICY  (first-party code)   │
        │  FastTriage · RouteDecision · Supervisor    │
        │  DAG validation · CapabilityGate            │
        │  LatencyBudgetManager · ReAct stop logic    │
        └──────────────────────┬──────────────────────┘
                               │ drives
        ┌──────────────────────▼──────────────────────┐
        │  EXECUTION SUBSTRATE  (LangGraph)           │
        │  StateGraph · Send · interrupt              │
        │  Checkpointer · streaming · LangSmith       │
        └─────────────────────────────────────────────┘
```

The rule:

> **LangGraph decides how execution is carried out. It never decides what should execute.**

---

## 2. Primitive Mapping

| LangGraph primitive | Serves | Replaces the hand-written |
|---|---|---|
| `StateGraph` + typed channels | P15-04 graph runtime, P19 meeting-prep graph | bespoke node executor |
| `Send` API | P15-05 and P16-04 parallel execution of independent tasks | manual `asyncio.gather` over `get_ready_tasks()` |
| `interrupt()` | P18-03 graph interrupt for approval | bespoke pause signal |
| Checkpointer (`AsyncPostgresSaver`) | P18-05 resume, P18-06 stale-action revalidation | resumability via `state_snapshot` |
| Conditional edges | P15-01 route dispatch, P16-06 bounded replan edge | manual branch dispatch |
| `astream` / `astream_events` | future response streaming | not yet implemented |
| LangSmith callback integration | P3-08 vendor tracing | manual exporter inside `trace_span()` |

`ExecutionPlan.topological_order()` and `get_ready_tasks()` are **retained**. They validate and schedule the plan; `Send` merely dispatches the batch that they select. Scheduling authority stays in first-party code.

---

## 3. What Must Remain First-Party

Each item below encodes an approved invariant that no prebuilt abstraction enforces.

| Component | Invariant it protects |
|---|---|
| `FastTriage` / `RouteDecision` | ADR 0002 — simple requests must reach a specialist with 0–1 LLM calls and must never touch the Supervisor (P15-07) |
| Supervisor structured planning | P16-01 — plans are typed `ExecutionPlan` objects, never free-form message exchanges |
| DAG validation | P16-03 — capability validity, dependency existence, cycle absence, task ceiling, budget, all checked deterministically |
| `CapabilityGate` | ADR 0006 — tool visibility is constructed per activation; prevention over prompt instruction |
| `LatencyBudgetManager` | ADR 0008 — reserve-before-call accounting across a parallel DAG |
| ReAct stop logic | ADR 0004 — bounded steps, tool ceiling, no-progress detection, recorded stop reason |
| `CapabilityRequest` handling | ADR 0009 — the orchestrator schedules the peer; the agent never does |

---

## 4. Rejected Alternatives

### 4.1. `langgraph-supervisor` / `create_supervisor` — REJECTED

Unmaintained upstream, and architecturally incompatible:

| Conflict | Requirement violated |
|---|---|
| Coordinates via message-history handoff, no dependency graph | P16-01, P16-03 structured plan with `depends_on` |
| Transfers control to one agent at a time; `parallel_tool_calls` defaults to `False` | MASTER_PLAN §13, P16-04 mandatory parallelism |
| Handoff is control transfer between agents | ADR 0009 peer-to-peer prohibition |
| Supervisor LLM sits in front of every request | ADR 0002 route bypass, P15-07 budget assertion |
| No insertion point for per-activation gating, budget reservation, replan bounds | ADR 0006, ADR 0008, P16-06, P16-07 |

### 4.2. Tool-wrapped subagents — REJECTED as the Supervisor mechanism

Upstream's current recommendation, and a better fit for ADR 0009 than handoff since the specialist never seizes control. Still rejected for P16: wrapping a specialist as a tool cannot express task dependencies and cannot guarantee that independent specialists run concurrently. Acceptable only where a specialist composes deterministic tools within its own domain.

---

## 5. Two Persistence Layers

P3 already ships an audit and telemetry layer. LangGraph's checkpointer adds a resumability layer. They are **not** redundant, and neither derives from the other.

| Concern | Owner | Contents |
|---|---|---|
| Resumability | LangGraph checkpointer | full thread state, pending interrupts, replay history |
| Observability / audit / budget | P3 tables | `assistant_runs`, `tool_executions`, `llm_executions`, `audit_events` |

Boundary rules:

1. `assistant_runs.state_snapshot` stays a **sanitized read-only projection** for human inspection. It is never a resume source.
2. The checkpointer is subject to the same redaction policy — external content and secrets must not become durable in raw form.
3. Redis keeps its P3 roles (cache, rate limit, session envelope). It does **not** become a second checkpoint store.
4. Audit tables remain append-only regardless of checkpoint rollback or replay.

---

## 6. Dependency Boundary

```text
app/domain/      → zero framework imports. Pure Pydantic contracts.
app/services/    → zero framework imports.
app/agents/      → may import LangChain model/tool interfaces only.
app/harness/     → the only layer permitted to import langgraph.
```

`AssistantState` uses `extra="forbid"` and `validate_assignment=True`. It is **not** passed to `StateGraph` unchanged; P11 must define an explicit channel schema with reducers and convert at the harness boundary. This keeps the domain contract independent of LangGraph's state-merge semantics.

---

## 6A. Two PostgreSQL Drivers

Deliberate, not accidental:

| Driver | Used by | Scope |
|---|---|---|
| asyncpg (via SQLAlchemy) | ORM models, migrations, domain queries | all application data |
| psycopg3 (`psycopg[binary]`) | `AsyncPostgresSaver` | checkpointer only |

`psycopg[binary]` is mandatory — the plain `psycopg` wheel expects a system libpq, which is not present on a Windows development host. The two drivers use different URL forms, so the checkpointer DSN is derived from `DATABASE__URL`:

```text
DATABASE__URL      postgresql+asyncpg://user:pw@host:5434/assistant
checkpointer DSN   postgresql://user:pw@host:5434/assistant
```

Do **not** migrate the data layer to psycopg, and do **not** hand-roll a checkpointer over asyncpg to avoid the second driver.

---

## 7. Introduction Timeline

| Phase | Substrate work |
|---|---|
| P3 | dependencies declared and locked; **no** `langgraph` import anywhere |
| P4–P10 | none; declaration remains unused |
| P11 | first permitted import; channel schema, reducers, checkpointer DSN derivation; ReAct loop on the substrate |
| P15 | `StateGraph` workflow runtime; `Send` fan-out for parallel nodes |
| P16 | dynamic DAG execution driven by validated `ExecutionPlan` |
| P18 | `interrupt()` plus `AsyncPostgresSaver` for durable approval pause/resume |

Locked versions at declaration time: `langgraph` 1.2.11, `langchain-core` 1.5.6, `langgraph-checkpoint-postgres` 3.1.2, `langsmith` 0.11.0, `psycopg` 3.3.4.
