# Phase P11 Review Pack: Specialist Agent Runtime + Bounded ReAct

> Phase: **P11 — SPECIALIST AGENT RUNTIME + BOUNDED REACT**
> Status: **IMPLEMENTED, TESTED, AND VERIFIED GREEN**
> Scope: **P11-00..P11-10 per `phases/P11_specialist_agent_runtime_bounded_react.md`**

---

## 1. Executive Summary

Phase 11 delivers the common runtime every specialist executes on. First-party
code owns all orchestration policy (mode selection, stop conditions, budgets,
capability gating, delegation, repeat guard); LangGraph is used strictly as
the execution substrate inside `app/harness/` per ADR 0011 / MASTER_PLAN §18A.
No domain prompts are implemented — those land with the P12/P13 specialists.

```text
SpecialistTask (+ AgentDefinition + gated ToolRegistryView)
    │
    ▼
ModeSelector: explicit task.mode else agent.default_execution_mode
    ├───► DIRECT ──► single LLM call, zero tools ──► report
    │        │ (tool calls requested → bounded escalation, once)
    │        ▼
    └───► BOUNDED_REACT ──► LLM → gate check → execute → observe ──► report
                              │ stop: success/no-progress/max-steps/
                              │   max-tools/budget/timeout/policy
                              ▼
SpecialistOutcome(report + trace + usage + needs_approval + depth)
```

---

## 2. Deliverables by Spec Item

| Spec | Artifact | Notes |
|---|---|---|
| P11-00 substrate | `app/harness/channels.py`, `dsn.py`, `graph.py` | `SpecialistChannels` TypedDict (required keys + `add` reducers); `AssistantState` never enters `StateGraph` — `state_to_channels` / `apply_channels_to_state` convert at the boundary; DSN strips `+asyncpg`/`+psycopg*`, rejects non-postgres; graph is select → execute_direct/execute_react → finalize, conditional edge on first-party selector |
| P11-01 direct | `SpecialistRunner` DIRECT branch | Exactly 1 LLM call, 0 tool executions |
| P11-02 ReAct | `SpecialistRunner` loop | LLM → tool → observation; usage accumulated into `BudgetUsage`; timeout via `wait_for` |
| P11-03 stops | `StopReason` enum + checks | success / no-progress / max_steps / max_tool_calls / budget / timeout / policy |
| P11-04 gate | Runner + `CapabilityGate` views | Runner receives an already-gated `ToolRegistryView`; unknown tools → error observation (never executed); mutations in read-only scope are invisible by construction |
| P11-05 trace | `ReActTraceStep` / `SpecialistTrace` | iteration / tool / sanitized args / observation summary / success; terminal stop reason; escalation + circuit flags; no chain-of-thought stored |
| P11-06 selector | `ModeSelector` + escalation | Explicit mode wins; bounded DIRECT→REACT escalation once when the direct turn requests tools (request itself is dropped, never executed) |
| P11-07 delegation | `DelegationService` | Single entry point: parent permission → child depth = parent+1 → reject over `min(budget, target.max_child_depth)` → scoped view via `ToolRestriction` → execute → collect `DelegationResult` |
| P11-08 context | `DELEGATION_CONTEXT` + scope line | Injected as system preamble before any domain prompt; `permit_mutations=False` pins approvals off |
| P11-09 guard | `RepeatToolGuard` | Identical (tool, args) failing/empty streak → reminder after 2 → circuit breaker after 3 (→ `NO_PROGRESS`); success or changed args resets |
| P11-10 report | `specialist.report` tool + models | Schema-validated `SpecialistReport`; invalid payloads become observations; stops without a report synthesize `BLOCKED` (or `NEEDS_APPROVAL` when pinned) |

Domain additions (framework-free): `StopReason`, `SpecialistStatus`,
`ChatMessage`, `AssistantTurn`, `ToolCallRequest`, `SpecialistReport`,
`ReActTraceStep`, `SpecialistTrace`, `SpecialistTask`, `SpecialistOutcome`,
`DelegationRequest`. LLM/tools enter only through injected `ChatBackend` /
`ToolExecutor` protocols — no keys, no network in tests.

---

## 3. Verification

- `tests/unit`: **648 passed** (42 new: 16 react + 9 delegation + 12 guard/report + 8 harness, incl. langgraph-boundary scan, DSN, round-trip, live graph runs).
- `ruff check app/ tests/`: clean. `pyright` on all touched files: 0 errors
  (repo-wide remainder is 14 pre-existing errors in untouched
  `tests/unit/services/test_retrieval_foundation.py`).
- Spec test mapping: direct 1-LLM/0-tool · react multi-tool · max steps ·
  no-progress · forbidden tool unavailable · budget exhausted · timeout ·
  round-trip lossless · DSN strips `+asyncpg` · no langgraph outside
  `app/harness/` · depth exceeded rejects · DELEGATION_CONTEXT injected ·
  NEVER pin blocks mutation + `needs_approval` · reminder + breaker — all green.

---

## 4. Definition of Done

```text
[x] channel schema with reducers; AssistantState boundary conversion both ways
[x] checkpointer DSN derivation from DATABASE__URL
[x] direct execution contract (1 LLM, 0 tools)
[x] bounded ReAct loop with all 7 stop conditions
[x] CapabilityGate integration (gated view only)
[x] trace format without chain-of-thought
[x] DIRECT/REACT selector + bounded escalation
[x] delegation depth enforcement via single entry point
[x] DELEGATION_CONTEXT injection + approval pin
[x] repeat-tool reminder + circuit breaker
[x] structured report channel with validation
[x] no langgraph import outside app/harness/
[x] no langgraph_supervisor / handoff / tool-wrapped subagents
[x] pytest + ruff + pyright green
[ ] user APPROVED P11
```

---

## 5. Transition

- Consumer P12 (Communication/Calendar specialists) provides domain prompts,
  real `ChatBackend`, and tool dispatch against this runtime unchanged.
- Consumer P13 (KnowledgeResearchAgent) calls
  `retrieval.factory.build_retrieval_pipeline()` from inside this runtime.
- P18 wires a real `AsyncPostgresSaver` into `SpecialistGraphBuilder.build()`.

---

## 7. Post-Review Fixes (H1–H2, M1–M6)

All 8 review findings verified against code and fixed:

| ID | Finding | Fix |
|---|---|---|
| H1 | Timeout discarded usage/trace (fresh empty objects) | `_RunState` owned by `run()`; timeout outcome carries real usage, steps, escalation/circuit flags |
| H2 | `ScriptedChat` repeated last turn forever | Strict by default (raises on exhaustion); `repeat_last=True` opt-in for unbounded-loop tests |
| M1 | All-failed multi-tool turns looped without terminal | Turn-level signature tracking stops repeats (subsumed by M6 fix) |
| M2 | `max_llm_calls` off-by-one (`>` allowed budget+1) | Pre-call `>=` enforcement — every paid response is processed, never exceeded |
| M3 | Executor exceptions bypassed the guard | Unified result path: exceptions feed `guard.observe(success=False)` like normal failures |
| M4 | `DelegationContext.read_only=False` contradicted the NEVER pin | Set `True` — mutations are rejected, so the child is strictly read-only in effect |
| M5 | Token budgets were dead code (never recorded) | `AssistantTurn` carries `prompt_tokens`/`completion_tokens`; recorded per turn into `BudgetUsage` |
| M6 | No-progress missed oscillation (A,B,A,B) | Whole-turn signatures with occurrence counts replace last-step-only comparison |

Verification after fixes: `tests/unit` **655 passed** (49 P11 tests), `ruff`
clean, `pyright` 0 errors on scope.

---

## 8. Independent Re-Review — Round 2 (findings & fixes)

A second full pass over the shipped P11 code (`app/agents/specialist/`,
`app/harness/`, P11 domain models, and unit tests) produced the following
verdicts, all fixed and re-verified green:

| ID | Severity | Finding | Fix |
|---|---|---|---|
| R2-H1 | HIGH | Non-terminal rejections in `_dispatch_call` (invalid `specialist.report` payload; tool not in the gated view) recorded a trace step but never appended a `role="tool"` message — the next LLM call carried a dangling `tool_call` without its result, which strict chat backends reject. Tests missed it because `ScriptedChat` does not validate transcript structure. | Both paths now append the rejection observation as a tool message; regression tests `TestTranscriptIntegrity` assert the follow-up prompt ends with the tool-role response. |
| R2-M1 | MEDIUM | Runner advertised mutation tools in `tool_schemas` even when the execution can never run them (`tools.is_read_only` or `permit_mutations=False`) — contradicts P11-04 least privilege, invites wasted/blocked calls. | Unusable mutation tools are filtered from the advertised schemas (`TestToolAdvertLeastPrivilege`); the runtime blocking check is unchanged (defense in depth). |
| R2-L1 | LOW | Stray garbage file `addopts=` at repo root (shell accident, untracked). | Deleted. |
| R2-L2 | LOW | `dsn.py` stripped driver markers anywhere in the URL (`replace` on the whole string) — credentials containing a marker substring would be corrupted. | Markers are stripped from the scheme segment only. |
| R2-L3 | LOW | `DelegationService.delegate` resolved the target agent and computed the scoped view twice (inside `build_child_task` and again in `delegate`). | View computed once and passed through `build_child_task(request, view=...)`. |
| R2-L4 | LOW | Dead parameters (`_record_step(messages, ...)` / `_complete(..., task)` both immediately `del`'d) and duplicated `SpecialistOutcome` construction (timeout path, loop-terminal path vs `_finish`). | Dead params removed; terminal paths unified onto `_finish`. |
| R2-L5 | LOW | `SpecialistRunner` did not validate guard thresholds at construction — misconfiguration surfaced mid-activation via `RepeatToolGuard.__post_init__`. | Constructor now fail-fast validates `repeat_remind_after` / `repeat_breaker_after` (guard validation retained). |
| R2-L6 | LOW | Repo-wide ruff (outside P11 scope, in uncommitted H1–H7 remediation files): `F811` shadowed `model_name` attribute in `ViRankerReranker` (dead class attribute vs property) and unused `Any` import in `app/services/spill.py`. | Both removed. |

Verification after Round 2: `tests/unit` **674 passed** (P11 scope 58, incl. 4 new
regression tests), `ruff check app tests` clean, `pyright` 0 errors on all
touched files.

Behavioral note (accepted, documented in code): the whole-turn no-progress
detector is intentionally stricter than `RepeatToolGuard` — identical
*successful* whole-turn repeats also count as a stall (deterministic
duplicates add no information), while the guard itself resets on success.

---

## 9. Independent Re-Review — Round 3 (external findings: risks + dead code)

Findings raised against the pre-`APPROVED P11` state, each verified against the
actual environment and either fixed, re-diagnosed, or accepted with rationale.

### 9.1 Environment (RISK-H1 / RISK-L1) — RE-DIAGNOSED AND FIXED

The original diagnosis ("Pillow 10.4.0 breaks the test suite on Python 3.14")
was **not reproducible on the working venv**: `.venv` runs CPython 3.14.0 with
Pillow 12.3.0, `import docling` succeeds, and the full unit suite passes.
The real breakage is in **`uv run`**: the stale `uv.lock` pinned
`pillow==10.4.0` + `pyyaml==6.0.2` (no CPython 3.13+/3.14 wheels; source
builds fail — observed as `Building pyyaml==6.0.2` aborting `uv run`).

Root cause chain (verified via `uv lock` conflict output):

- `surya-ocr>=0.17.0` (optional `ocr-surya` extra) requires `pillow>=10.2.0,<11.0.0`;
- `paddleocr→paddlex` (optional `ocr-paddle` extra) pins `pyyaml==6.0.2` exactly;
- uv's universal resolution covers ALL extras, so those caps leaked into every
  resolution split and forced the old versions globally.

Fixes applied:

1. `pyproject.toml`: `requires-python = ">=3.11,<3.15"` (upper bound for
   stability; `<3.14` was rejected because the working venv IS 3.14).
2. Explicit floors `pillow>=11.0`, `pyyaml>=6.0.3` for the docling chain.
3. `[tool.uv] override-dependencies = ["pillow>=11.0", "pyyaml>=6.0.3"]` —
   lifts the OCR-extras caps. Safe because OCR extras are isolated from the
   runtime per P9E-2 (never imported by `app/`; separate strong-GPU machine).
4. `uv.lock` re-resolved: pillow 12.3.0, pyyaml 6.0.3.

Verification: `uv sync` + `uv run python -c "import docling, yaml, PIL"` → OK
(12.3.0 / 6.0.3); `uv run pytest tests/unit/agents tests/unit/harness` → 58
passed. Caveat: `paddlepaddle` (ocr-paddle extra) still has no cp314 wheel —
the OCR machine must use Python ≤3.13, consistent with its isolation contract.

### 9.2 Dead code (DEAD-H1..H4) — FIXED

`AgentRequest`, `AgentResult`, `NeedMoreContext`, `CapabilityRequest` (P02-era
contracts superseded by the P11 `SpecialistTask`/`SpecialistOutcome`/
`SpecialistReport` family, with zero production consumers) are removed:

- `DelegationResult` is now a standalone `BaseModel` (was `AgentResult`
  subclass); `DelegationService` and its tests are unchanged in behavior.
- Removed from `app/domain/models/__init__.py` exports.
- `tests/unit/domain/test_agent.py` updated: dead-model tests removed; a
  `TaskResult`+evidence test retained (still consumed by `AssistantState`).

### 9.3 Other code fixes

| ID | Finding | Fix |
|---|---|---|
| RISK-M4 | `apply_channels_to_state` read usage via loose string fallbacks (`"tokens"`, `"cost"`, …) — schema drift would silently contribute zeros. | Usage channel now validated through `BudgetUsage.model_validate` — unknown/mistyped keys fail loudly at the harness boundary. |
| RISK-M5 | `permit_mutations` gating duplicated in `state_to_channels` and the runner. | Kept (defense in depth) but the docstring now names the enforcement points and requires the two stay consistent. |
| RISK-L2 | Kahn's algorithm used `list.pop(0)` (O(n)). | `collections.deque` + `popleft()` in `workflow.py` (cycle + reachability) and `plan.py`. |
| RISK-L3 | 14 pre-existing pyright errors in `test_retrieval_foundation.py`. | Fixed: `make_query(**overrides: Any)`, `chunk(kind: Literal["dense","sparse","hybrid"])`, `# type: ignore[arg-type]` on the `Slow` doubles (same pattern as the existing line 290 ignore). Pyright now 0 errors repo-touched-wide. |
| R2-L6 follow-up | `ExecutionBudget` import left unused in `agent.py` after the dead-model removal. | Removed. |

### 9.4 Accepted risks / forward declarations (documented, no code change)

| ID | Verdict | Rationale |
|---|---|---|
| RISK-H2 | ACCEPTED (by design) | `ChatBackend`/`ToolExecutor` are P11 seams; P12 wires the real LLM adapter and tool dispatch. Protocols are frozen in tests; a P12 signature change would be a reviewed interface change, not silent drift. |
| RISK-H3 | ACCEPTED (by design) | Checkpointer deferred to P18 per spec (`SpecialistGraphBuilder.build(checkpointer=...)` hook already exists). No production deployment is authorized before P18; until then, mid-execution crashes lose in-flight specialist state only. |
| RISK-M1/M2/M3, RISK-M6, DEAD-L1/L2 | MITIGATED (verified) | `deepseek-harness/`, `TencentDB-Agent-Memory/`, `scratch/`, `data/`, `/models/` are untracked and gitignored; there is no `.gitmodules` (TencentDB-Agent-Memory is a plain ignored nested clone, not a submodule); `docker-compose.yml` defines only postgres/redis — no app build context, so nothing can leak into an image. Physical deletion of reference dirs is left to the owner (out of repo scope). |
| DEAD-L3 | MITIGATED (verified) | `data/` is gitignored and untracked. |
| DEAD-M1/M2 (skill.py, workflow.py) | KEPT | Forward-declared contracts for P14/P15/P19 with their own passing unit tests; removing approved-phase contracts would violate the no-phase-skipping gate. Schema mismatch risk is bounded by those tests. |
| DEAD-M3/M4 (plan field, route_decision in `AssistantState`) | KEPT | Same rationale; consumed by P16/P15. |
| DEAD-M5 (`inherits_parent_tools`) | DOCUMENTED | Field description now states explicitly it is reserved for P12+ and NOT enforced by `DelegationService`; no behavior was invented mid-review. |
| DEAD-M6 (`normalized_request`) | KEPT | Lifecycle field of the state contract (persisted, audited); set by the future normalization step. |

### 9.5 Verification after Round 3

- Full unit suite (chunked): **671 passed, 0 failed** (674 − 4 removed
  dead-model tests + 1 replacement `TaskResult` test).
- `ruff check app tests`: clean. `pyright` on all touched files: 0 errors.
- `uv run pytest` smoke: 58 passed (previously `uv run` could not even build
  the environment).

---

## 10. Closure Command

To formally close Phase 11 and authorize Phase 12:
👉 **`APPROVED P11`**
