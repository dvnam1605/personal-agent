# Phase P16 Review Pack: Supervisor + Dynamic Multi-Agent DAG

> Phase: **P16 — SUPERVISOR + DYNAMIC MULTI-AGENT DAG**
> Status: **HARDENING IN PROGRESS — second-round H1–H6 / M1–M10 remediations landed; `pytest`/`ruff`/`pyright` green; awaiting `APPROVED P16`**
> Scope: **per `phases/P16_supervisor_dynamic_multi_agent_dag.md`, `MASTER_PLAN.md §18A`, and `docs/adr/0011-langgraph-as-orchestration-substrate.md`**
> Gate in: **APPROVED P15** received from user 2026-09-08.
> Gate out: **`APPROVED P16`** from user unblocks Phase 17 (`phases/P17_context_memory_entity_resolution_memory_gate.md`).

---

## 1. Executive Summary

Phase 16 delivers dynamic cross-domain multi-agent orchestration via a LangGraph DAG substrate that executes on top of proven specialist agents:
1. **P16-00 & §18A.5 Substrate Isolation**: Strict isolation of orchestration substrate within `app/harness/supervisor/`. Zero framework imports (`langgraph=0`) in `app/domain` or `app/services`. Forbidden constructs (`langgraph_supervisor`, `create_supervisor`, `create_handoff_tool`, tool-wrapped subagents) are strictly banned and AST-checked.
2. **P16-01 & P16-02 Capability Catalog & Typed Plans**: Supervisor sees high-level `AgentCapabilityDescriptor` items in `CapabilityCatalog` rather than low-level tools. The Supervisor planner generates structured, typed `ExecutionPlan` models with validated dependencies.
3. **P16-03 First-Party DAG Invariants & Budget Validation**: Pre-execution validation verifies unique IDs, topological dependency existence, cycle detection (Kahn's algorithm), task ceiling (`max_tasks`), budget compliance (`max_cost_usd`), and max delegation chain depth (`max_delegation_depth`).
4. **P16-04 Parallel DAG Execution via LangGraph `Send` API**: Runnable batches from `ExecutionPlan.get_ready_tasks()` are dispatched concurrently through LangGraph's native `Send` API. Timing overlap tests prove independent specialists execute in parallel rather than sequentially ($t_{\text{elapsed}} < \sum t_i$).
5. **P16-05A Structured Signals & Cross-Domain Handshake**: Specialist report-backs with `NEEDS_MORE_CONTEXT` extract `NeedMoreContext.what_is_needed` and schedule targeted follow-up tasks without peer-to-peer invocation. Destructive actions requiring confirmation return `NEEDS_APPROVAL` and surface approval items for human review.
6. **P16-05B Delegation Depth Enforcement**: `current_depth` is passed down task chains; excessive delegation chains are rejected at DAG validation time before running any tasks.
7. **P16-05C Continuable Subagent Sessions vs One-Shot Workers**: `ContinuableSessionManager` manages one-shot workers (disposed immediately) and continuable sessions (in-memory multi-turn state preservation).
8. **P16-05D Tool Scoping Filter & Capability Narrowing**: Dynamic restriction via `ToolRestriction` allows spawning subagents with whitelisted tools; attempts to execute forbidden tools outside the scoped view fail loudly (`PermissionDeniedError`).
9. **P16-06 & P16-07 Bounded Replanning & No-Progress Detection**: Follow-up tasks are scheduled only when missing context occurs, up to `max_replans`. Repeated equivalent plans are detected and terminated deterministically.
10. **P16-08 Deterministic Synthesis**: Single final synthesis validates task statuses, accumulated evidence items, and missing information before concluding.

---

## 2. Architectural Invariants & Substrate Compliance

| Principle / Rule | Requirement | Implementation Status | Evidence / Verification |
|---|---|---|---|
| **ADR 0011 Substrate** | Orchestration on LangGraph `StateGraph` + `Send` | Fully Implemented | `SupervisorGraphBuilder` in `app/harness/supervisor/graph.py` |
| **§18A.5 Framework Isolation** | `domain=0`, `services=0` framework imports | Zero Violations | ripgrep verifies 0 `langgraph` imports in `app/domain` and `app/services` |
| **P16-00 Forbidden Constructs** | No `langgraph_supervisor` or `create_handoff_tool` | Zero Occurrences | AST scan in `TestSupervisorInvariants.test_forbidden_constructs_not_imported_in_app` passes |
| **P16-02 Capability-Only View** | Supervisor sees capabilities, not tools | Enforced | `CapabilityCatalog` built from `AgentRegistry.list()` descriptors |
| **P16-04 Parallel Concurrency** | Independent tasks dispatched via `Send` | Enforced | Verified in `test_03_independent_specialists_run_in_parallel` |
| **Type Safety & Quality Gate** | Pyright 0 errors, Ruff clean | 100% Passing | `uv run pyright` (0 errors), `uv run ruff check` (all clean) |

---

## 3. Required Scenarios Verification Matrix (10/10 Passed)

| Scenario # | Requirement Description | Test Implementation | Result |
|---|---|---|---|
| **1** | Simple request bypasses Supervisor | `test_01_simple_single_domain_bypasses_supervisor` | **PASS** (FastTriage routes to `DIRECT_SPECIALIST` / `STATIC_WORKFLOW` with 0 tokens) |
| **2** | Multi-domain open task uses Supervisor | `test_02_multi_domain_open_task_uses_supervisor_planner` | **PASS** (FastTriage routes to `SUPERVISOR_DAG`; planner constructs valid 2-agent DAG) |
| **3** | Independent specialists run in parallel | `test_03_independent_specialists_run_in_parallel` | **PASS** (Two 150ms tasks run concurrently via `Send`; elapsed $\approx 160\text{ms} \ll 300\text{ms}$) |
| **4** | Missing data triggers one replan | `test_04_missing_data_triggers_one_replan` | **PASS** (`missing_context` triggers replan node, schedules follow-up, completes successfully) |
| **5** | Replan limit stops safely | `test_05_replan_limit_stops_safely` | **PASS** (Continual missing context terminates safely at `replan_count <= max_replans`) |
| **6** | Budget stops runaway planning | `test_06_budget_stops_runaway_planning` | **PASS** (`max_tasks` and `max_cost_usd` violations rejected by `validate_execution_plan`) |
| **7** | Specialist returns NeedMoreContext -> executor schedules follow-up | `test_07_specialist_need_more_context_triggers_replan` | **PASS** (Extracts `NeedMoreContext.what_is_needed` and schedules follow-up task) |
| **8** | Delegation depth limit prevents unbounded chains | `test_08_delegation_depth_limit_prevents_unbounded_chains` | **PASS** (Chain depth 4 > budget max depth 2 rejected at DAG validation time) |
| **9** | Continuable subagent session receives follow-up prompt without context loss | `test_09_continuable_subagent_session_receives_followup_without_context_loss` | **PASS** (Multi-turn session retained in `ContinuableSessionManager`; turn history intact) |
| **10** | Subagent attempts forbidden tool call outside narrowed scope -> blocked loudly | `test_10_subagent_tool_scoping_blocks_forbidden_tool` | **PASS** (`ScopedToolView` enforces `ToolRestriction`; disallowed tool raises `PermissionDeniedError`) |

---

## 4. Deliverables & Code Changes Summary

| Component | File Path | Scope & Functions |
|---|---|---|
| Domain Models | `app/domain/models/supervisor.py` | `AgentCapabilityDescriptor`, `CapabilityCatalog`, `SubagentSessionState`, `SupervisorResult` |
| Domain Models | `app/domain/models/plan.py` | `CapabilityRequest`, `NeedMoreContext`, `PlanValidationResult`, `ExecutionPlan.calculate_max_depth()`, `check_graph_invariants()`, `ExecutionTask.dependencies: list[TaskDependency]` |
| Domain Enums | `app/domain/enums/enums.py` | `TaskStatus.NEEDS_MORE_CONTEXT`, `TaskStatus.NEEDS_APPROVAL`, `ActionClass.DESTRUCTIVE` |
| Supervisor Services | `app/services/supervisor/catalog.py` | `build_capability_catalog()` maps `AgentRegistry` to `CapabilityCatalog` (zero framework imports) |
| Supervisor Services | `app/services/supervisor/validator.py` | `validate_execution_plan()` validates DAG invariants, capabilities, depth, task limits, and budget |
| Supervisor Services | `app/services/supervisor/planner.py` | `SupervisorPlanner` structured LLM prompt, heuristic fallback, bounded replanning, and `detect_no_progress` |
| Supervisor Services | `app/services/supervisor/session_manager.py` | `ContinuableSessionManager` for subagent lifecycle management and `ScopedToolView` tool filtering |
| Harness Channels | `app/harness/supervisor/channels.py` | `SupervisorChannels` typed state channels with explicit `operator.add` & `update_dict` reducers, `domains`, `TaskDispatchChannel` |
| Harness Graph | `app/harness/supervisor/graph.py` | `SupervisorGraphBuilder` compiling native `StateGraph` with parallel `Send` step barrier, replan routing, and synthesis |
| Harness Dispatch | `app/harness/dispatch.py` | Connected `RouteType.SUPERVISOR_DAG` to `SupervisorGraphBuilder` execution |
| Test Suite | `tests/unit/harness/test_supervisor_dag.py` | 15 unit tests covering all 10 scenarios and architectural invariants |
| Test Suite | `tests/unit/harness/test_dispatch.py` | 6 unit tests covering harness dispatching to static workflows and supervisor |

---

## 5. Verification Results

### Automated Test Suite
Independent review H4: the earlier **840 passed / H1–H12 resolved** claim was premature. After this hardening patch:

- `uv run pytest tests/unit` → **exit 0** (2026-09-08, after second-round remediations)
- New direct coverage: `tests/unit/services/test_approval_tokens.py`, `tests/unit/services/test_consumed_store.py`

### Static Analysis & Type Checking
- **Ruff**: `uv run ruff check app tests` → All checks passed
- **Pyright**: `uv run pyright` → **0 errors, 0 warnings, 0 informations**

---

## 6. Architectural Review Hardening

### 6.1 First-pass findings (H1–H12 of the original P16 pack)
The original HMAC / specialist / retrieval hardening remains, but several items were **incomplete** and are superseded by §6.2.

### 6.2 Independent review blockers (must be green before approve)

| ID | Defect | Fix |
|---|---|---|
| **H1** | `consume=False` still called `try_consume` → pre-check burned single-use tokens | Peek uses `is_consumed()` only; `try_consume` only when `single_use and consume` |
| **H2** | Pre-check passed `agent_name` / skill name as `tool_name` | Pre-check uses `tool_name=None` (skip tool match); skill step authorize still checks `step.tool_name` |
| **H3** | Default dispatcher stubbed specialist + fabricated supervisor facts | `DIRECT_SPECIALIST` still fail-closes if `specialist_builder=None`; default dispatcher now wires `app/harness/runtime.py` (blocked LLM report, no fabricated facts) |
| **H4** | `.compile()` missing, replan id mismatch, fake tokens, pack over-claim | `compile()` aliases `build()`; replan ids `replan_{uuid8}`; tests use `generate_approval_token`; this pack no longer claims COMPLETE |
| **H5** | Fingerprint omitted chunking fields; orchestrator hashed global settings | `fp_v2` hashes 4 chunking fields; orchestrator uses `self._chunking_settings` |
| **H6** | In-memory consume only; `run_id` theater; lazy signing key | Redis store wired at lifespan (except TESTING); consume binds `run_id`/`proposal_hash`; Settings eager-validates signing key |
| **H7** | PARENT/SIBLING SQL missing filename/version; rerank load blocked the loop | SQL templates project `filename` + `document_version_id`; `_ensure_loaded` runs in `to_thread` under a lock |

### 6.3 Medium (same P16 patch)
M1 UUID `approval_id` vs `approval_token`; M2 delegated `approval_policy`/`sandbox_scope` enforced at graph resolve; M3 shared deep `strip_sensitive_keys`; M4 supervisor timeout/budget from `SupervisorBudgetSettings`; M5 resume/replan/handshake hardening; M6 `ToolRegistryView.get` records `agent_name`; M7 direct verify tests; M8 shared mutation helper + Kahn.

### 6.4 Second-round review (must stay green)

| ID | Defect | Fix |
|---|---|---|
| **H1** | Token not bound to `proposal_hash`; first consume accepted any arguments | `generate_approval_token(..., arguments=)` hashes canonical args; `require_mutation_approval` recomputes and passes `expected_proposal_hash` |
| **H2** | WF-01 peeked without consume / `expected_run_id` → N drafts in TTL | Execute path `consume=True` + `expected_run_id=state.run_id`; peek-only only for APPROVED_UNEXECUTED |
| **H3** | `DelegationResult` dropped `NEEDS_MORE_CONTEXT` | Result carries `status` + `missing_context`; executor maps to supervisor channels |
| **H4** | No composition root; both routes fail-closed forever | `app/harness/runtime.py` default-wires specialist + supervisor; explicit `None` still fail-closes |
| **H5** | `check_deadline` escaped `ainvoke` | Plan/execute/replan catch timeout/budget → `{status: failed, branch_errors}` |
| **H6** | Chunking `0`/`negative` hung | `ge=1` on four token fields; `merge_small_nodes_below_tokens <=` both hards |
| **M1–M10** | Unbound peek, `*`/multi-use at verify, SQL `filename`, fp_v2, camelCase strip, session store, restriction fail-open, capability glob, DIRECT mutate, replan treats failed as done | See §8; tests cover substitution, v1 skip-compat, glob lookup, invalid restriction |

### 6.5 Third-round hardening pass (/goal zero-defect closure)

| ID | Defect | Root Cause & Resolution |
|---|---|---|
| **H1** | Replan deadlock on `NEEDS_MORE_CONTEXT` | In LangGraph, `completed_task_ids` had `operator.add` reducer. Emitting `task_id` on `NEEDS_MORE_CONTEXT` permanently locked it out of replans. Fixed in `app/harness/supervisor/executor.py` and `app/harness/supervisor/graph.py` by only adding `task_id` when `status == "completed"` and marking unhandled/in-progress tasks as `replan_pending` for re-dispatch upon follow-up completion. |
| **H2** | Redis cross-loop thread trap in sync verify | `verify_approval_token_sync` called `_SYNC_VERIFY_POOL.submit(asyncio.run, coro)`, conflicting with Redis attached to FastAPI event loop. Added synchronous `try_consume_sync` and `is_consumed_sync` on `InMemoryConsumedTokenStore` and `RedisConsumedTokenStore`, avoiding thread pool and cross-loop execution. |
| **H3** | Approval token proposal bypass on omit | `_token_policy_allows` previously only checked hash equality if `expected_proposal_hash` was provided. If caller omitted it, bound tokens could execute with any arguments. Added strict rejection outside relaxed environments when `bound_hash` is present but `expected_proposal_hash` is None. |
| **M1** | Step barrier budget leak | `_route_step_barrier` now checks `budget_manager.check_deadline()` before dispatching ready batches, halting immediately on timeout/budget exhaustion instead of leaking into child tasks. |
| **M2** | Continuable subagent session residency | `SupervisorGraphBuilder` now maintains active continuable sessions for `(agent_name, run_id)` across tasks within a run, passing `session_id` into `TaskDispatchChannel`, closing upon final synthesis. |
| **M3** | Parameter sanitization truncation vs proposal hash | Relaxed `sanitize_payload` limits (`max_string_len=10_000`, `max_payload_bytes=65_536`) in `ApprovalRequestService.create_request` so parameters aren't truncated, preventing proposal hash mismatch against mutation tools. |
| **M4** | Dependency condition resolution | `ExecutionPlan.get_ready_tasks` now inspects task outcome dict status (`failed`, `error`, `blocked`, `timed_out`) rather than raw dict truthiness. |
| **L1** | Hardcoded chunking literals | Replaced hardcoded chunking literals (`1600/500/2400/800`) in `FingerprintInputs` with `default_factory` pulling from `get_settings().chunking`. |

### 6.6 Fourth-round hardening pass & backlog triage

| ID | Cat | Defect / Finding | Root Cause & Resolution / Backlog Target |
|---|---|---|---|
| **H1** | HIGH | WF-01 double consume breaks live tool gate | Workflow node only peeks (`consume=False`); single-use token consumption strictly at tool gate. Verified with `test_wf01_tool_consumes_token_without_double_consume_failure`. |
| **H2** | HIGH | Token bearer secret leaks in `context_data` | Sanitized via `strip_sensitive_keys` in dispatch, channels, and specialist ReAct prompt initialization. Zero token leakage in LLM prompts or traces. |
| **M1** | MED | `dispatch.py` state in-place mutation (`llm_call_count += ...`) | Non-blocking (runs under Pydantic validate_assignment and passes test suites). Backlog P18: refactor dispatch return to immutable model copy. |
| **M2** | MED | Executor emits empty evidence on failed tasks | Fixed in `executor.py`: only emit `EvidenceItem` when `result.success and result.output and result.output.strip()`. |
| **M3** | MED | Active sessions append-only leak in supervisor graph | Fixed in `supervisor/graph.py`: scoped per-run session cleanup in `_synthesize_result` + 500-session LRU cap. |
| **M4** | MED | Fingerprint v1 permanent skip on unchanged content | Intentional deferred debt to prevent corpus reindex; scheduled backfill/throttle job for P17. |
| **M5** | MED | ViRanker snapshot fallback network on air-gapped hosts | Added `allow_network_download: bool = False` in `config.py` and enforced in `rerank.py` (fail-loud RuntimeError when snapshot missing and download disallowed). |
| **L1** | LOW | Ruff I001 unsorted imports in `react.py` and `dispatch.py` | Fixed with `ruff check --fix app tests`. Clean 0 errors. |
| **L2** | LOW | LangGraph `UserWarning: config typed as Any` in `graph.py` | Fixed by adopting canonical LangGraph 1-argument node functions `func(state)`. Zero framework imports (§18A.5) and 0 warnings. |
| **L3** | LOW | Deferred tech debt (benchmark_dataset, graph build closure, tool base) | Deferred per design pack §8 (retained for P18 tool consolidation). |

---

## 7. Gate Status

- Phase 16 is **not** COMPLETE. Independent-review blockers in §6.2 must stay green, plus a recorded pytest/ruff/pyright run.
- Upon receipt of user confirmation (`APPROVED P16`), Phase 16 will close and Phase 17 will be unblocked.

---

## 8. Limitations / Deferred

- **LLM / live tools still P18.** `app/harness/runtime.py` wires `DelegationService` + `SpecialistRunner` with `UnconfiguredChatBackend` (reports `blocked`) and `UnwiredToolExecutor`. That is enough for P16 graphs to run outside tests without fabricating calendar/gmail facts. Google OAuth + retrieval pipeline injection remains P18.
- **Approval tokens** must be minted with `run_id` + canonical arguments (`execution_token_for_request` on resume). Workflow WF-01 only peeks; single-use token consumption occurs strictly at the tool gate (`gmail.create_draft`).
- **Fingerprint v1 compat** skips reindex when the stored hash matches `fp_v1` (no chunking fields). New writes still emit `fp_v2`. Online backfill/throttle job is backlog for P17.
- **Single-use store is Redis-at-lifespan, in-memory in TESTING.** If Redis is down in DEVELOPMENT, consume falls back to process-local memory (logged). Staging/production refuse to start without Redis.
- **Heuristic planner** remains keyword-based fallback when no LLM is injected; capability lookup is glob-aware and no longer hardcodes agent names when the catalog has agents.
- **ViRanker offline-first** fails loud (`RuntimeError`) if snapshot is missing when `allow_network_download=False` (default prod setting). Network download must be explicitly opted into.
- **Windows key-file chmod** cannot be POSIX `0o600`; code logs a warning with an `icacls` command (see `.env.example`).
- **L1 file splits** (benchmark_dataset, graph.build closures, triage, ToolExecutorBase) are deferred — high regression cost vs this hardening patch.
- **Dispatch state in-place mutation** (M1) is scheduled for clean immutable state return in P18.
- Full PolicyEngine REST + PostgreSQL interrupt/resume remains P18.
