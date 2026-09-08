# Phase P15 Review Pack: Fast Triage + Static Workflow Registry

> Phase: **P15 — FAST TRIAGE + STATIC WORKFLOW REGISTRY**
> Status: **APPROVED (2026-09-08) — CLOSED**
> Scope: **per `phases/P15_fast_triage_static_workflow_registry.md` & `MASTER_PLAN.md §18A.5`**
> Gate in: **APPROVED P14** received from user 2026-09-07.
> Gate out: **APPROVED P15** received from user 2026-09-08 ("ok approved p15"). Unblocks Phase 16.

---

## 1. Executive Summary

Phase 15 delivers the core routing efficiency and static graph execution substrate of the personal AI assistant:
1. **FastTriage Engine**: Deterministic-first routing classifier (`app/services/triage.py` + `app/services/triage_rules.py`) that strictly satisfies the Hard Invariant (§6.1): single-domain queries ("Lịch ngày mai?", "Email của Nam?", "Tìm quyết định 123") route directly to single-domain specialists with **0 Supervisor LLM tokens** and $\le 10\text{ms}$ latency.
2. **RouteDecision Contract**: Canonical, typed routing output (`app/domain/models/route.py`) with auditability, confidence scores, and parameter extraction, backwards-compatible with existing P2 tests and distinct legacy enum wire values (`known_workflow`, `supervisor`). Single source of truth for route type predicates resides cleanly in `app/domain/enums`.
3. **Static Workflow Registry (§18A.5 Compliant)**: Clean metadata registry (`app/services/workflow_registry.py`) with zero framework dependencies (`domain=0`, `services=0`). All compiled LangGraph workflows reside exclusively in `app/harness/workflows/` (`meeting_followup.py`, `document_briefing.py`).
4. **Harness Route Dispatcher**: Dedicated execution bridge (`app/harness/dispatch.py`) linking `RouteDecision` to compiled static subgraphs or Phase 16 supervisor orchestrator stubs, merging parameters safely so sensitive tokens (`approval_token`) are never lost.
5. **Parallel `Send` API with Tenant Isolation**: Parallel branch execution across independent domains (RAG and Drive concurrently in WF-02) with state accumulation via `operator.add` reducers, propagating tenant context (`user_id`, `run_id`).
6. **Fail-Closed Governance**: Strict rejection of fabricated drafts; mutation workflows without an approval token safely return `needs_approval` and never execute injected tools.

---

## 2. Review Findings Remediation Matrix (Rounds 1, 2, and 3)

| Code | Severity | Item | Problem Identified | Resolution Implemented |
|---|---|---|---|---|
| **H1** | **HIGH** | `\bthu\b` & `thu tu` collisions | "thứ" (mail vs weekday) and "thứ tư" (Wed) vs "thứ tự" (order) collided on unaccenting | Removed bare `\bthu\b`. Weekdays only activate with calendar signals. Explicit `ORDER_PHRASE` guard (`theo thu tu`) disables Wednesday. "Hộp thư" excluded before checking calendar "họp". |
| **H2** | **HIGH** | WF-01 fail-closed bypasses | Missing token still called injected draft creator; token with missing tool fabricated draft_id | Missing token immediately returns `status="needs_approval"` without calling draft creator tool. Missing tool with token returns `status="approved_unexecuted"` with `draft_id=None`. |
| **H3** | **HIGH** | WF-02 conflict guard too narrow | `has_comm and not has_research` allowed "tra cứu và tóm tắt email của Nam" to reach WF-02 | Unconditionally blocks WF-02 whenever any email signal (`has_comm`) is present. Email search verbs resolve to `CommunicationAgent`. |
| **H4** | **HIGH** | Jailbreak bypass via "tài liệu" | "ignore previous instructions trong tài liệu" bypassed reject because "tài liệu" was in inquiry pattern | Separated prompt attacks (`PROMPT_ATTACK_PATTERN`) from destructive SQL/bash commands. Prompt attacks are **unconditionally rejected** without exceptions. |
| **H5** | **HIGH** | §18A.5: LangGraph import in `services` | `app/services/workflow_registry.py` imported `langgraph.graph` and `langgraph.types.Send` | Moved graph builders to `app/harness/workflows/`. `services/workflow_registry.py` contains 0 framework dependencies. |
| **M1** | **MEDIUM** | Natural "thư" phrases swallowed | Removing bare `\bthu\b` broke natural phrases like "thư của Nam", "đọc thư mới" | Added natural multi-word phrases: `doc thu`, `thu moi`, `thu cua`, `xem thu`, `kiem tra thu`, `hop thu`, `thu den`, `thu di` to `COMMUNICATION_PATTERN`. |
| **M2** | **MEDIUM** | Follow-up after meeting misrouted | "Please follow up after the meeting with Nam" routed to CalendarAgent due to `\bmeeting\b` | Added `FOLLOWUP_AFTER_MEETING`: follow-up directed to a recipient after a meeting overrides calendar context and routes to `CommunicationAgent`. |
| **M3** | **MEDIUM** | Duplicate `RouteType` values & wire round-trip | `KNOWN_WORKFLOW = "static_workflow"` caused `RouteType("known_workflow")` to raise ValueError | Retained distinct string values `KNOWN_WORKFLOW = "known_workflow"` and `SUPERVISOR = "supervisor"`. Model validator reconciles semantics without breaking wire round-trip. |
| **M4** | **MEDIUM** | Skill match swallowed calendar queries | "chuẩn bị họp ngày mai" was swallowed by `meeting-prep` skill into Supervisor | Calendar schedule queries (with relative dates/inquiries) preserve CalendarAgent fast-path. Skill routing checks cross-specialist capability combinations. |
| **M5** | **MEDIUM** | False reject & workflow domains | "DROP TABLE trong tài liệu nội bộ" was falsely rejected; workflows assigned `GENERAL` domain | Refined regex so definitional queries pass; WF-01 assigned `[CALENDAR, COMMUNICATION]`, WF-02 assigned `[KNOWLEDGE_RESEARCH]`. |
| **N1** | **MEDIUM** | Duplicate trigger matching semantics | Local `match_workflow_trigger` in `workflow_registry.py` duplicated `skills/matching.py` | Removed local copy. Centralized on `app/services/skills/matching.py:match_trigger` with explicit `allow_token_subset=False` for strict workflows & triage, `allow_token_subset=True` for skill registry. |
| **N2** | **MEDIUM** | Token loss in `harness/dispatch.py` | Query and parameters split caused potential drop of `approval_token` | Signature updated to `dispatch(decision, state, parameters=None)` with `{**decision.parameters, **(parameters or {})}` merging, query validation, injectable graph builders, and unit test suite. |
| **N3** | **MEDIUM** | Stale P15 review pack | Review pack out of date after architectural moves | Regenerated §3 and §4 with exact locations (`harness/workflows/`, `triage_rules.py`, `dispatch.py`), pyright clean verification, and actual test metrics. |
| **N4** | **LOW** | Bare `tim` in `RESEARCH_LOOKUP_PATTERN` | Bare `tim` matched non-research queries like "trái tim" or "tìm giúp tôi" | Removed bare `tim`, preserved `tim kiem|tim hieu|tra cuu|tra van`, and added `TIM_PREFIX_PATTERN` guard for document terms in multi-domain queries. |
| **N5** | **LOW** | Parameterless `batch_cb` lambda in ingestion | `batch_cb = lambda: heartbeat_fn(obs.job_id)` raised `TypeError` when called with args `(i, n)` | Updated to `lambda *a, **k: heartbeat_fn(obs.job_id)`. |
| **N6** | **LOW** | Triple re-export helper | `is_workflow_route`/`is_supervisor_route` re-exported across enums, models, and route | Kept definition only in `app/domain/enums`. Removed re-exports from `app/domain/models`. |

---

## 3. Deliverables Summary

| Component | File Path | Scope & Functions |
|---|---|---|
| Contract | `app/domain/models/route.py` | `RouteDecision`, `RouteType`, backward-compatible validators, clean exports |
| Enums | `app/domain/enums/enums.py` | `RouteType` with distinct legacy string values; canonical predicate helpers |
| Channels | `app/harness/workflow_channels.py` | `WorkflowState` with `branch_results` and `errors` reducers |
| Dispatcher | `app/harness/dispatch.py` | `HarnessDispatcher` execution bridge for static workflows and supervisor stubs |
| Static Subgraphs | `app/harness/workflows/meeting_followup.py` | WF-01: Quick Meeting Follow-up StateGraph (`fetch_calendar` -> `fetch_emails` -> `draft_followup`) |
| Static Subgraphs | `app/harness/workflows/document_briefing.py` | WF-02: Document Search & Briefing StateGraph (parallel `Send` to RAG and Drive -> `synthesize_briefing`) |
| Workflow Registry | `app/services/workflow_registry.py` | `StaticWorkflowRegistry`, `StaticWorkflowEntry`, metadata & trigger matching (0 framework imports) |
| Triage Rules | `app/services/triage_rules.py` | Compiled regex patterns for prompt attack, calendar, communication, and research |
| Triage Engine | `app/services/triage.py` | `FastTriage` (Stage 1 regex, Stage 2 heuristic, skill discovery, conflict guards, prompt attack filters) |
| Trigger Matching | `app/services/skills/matching.py` | Centralized `match_trigger`, `trigger_matches`, `match_workflow_trigger` with explicit subset control |
| Unit Tests | `tests/unit/harness/test_dispatch.py` | 6 unit tests: WF-01 needs_approval, extra parameter merging, WF-02 partial_error, unknown workflow, terminal routes |
| Unit Tests | `tests/unit/services/test_triage.py` | 18 unit tests: H1-H4, M1, M2, M4, M5 circuit breakers, multi-domain supervisor routing |
| Unit Tests | `tests/unit/services/test_triage_matrix_200.py` | 200+ Vietnamese query test matrix verifying $\ge 70\%$ supervisor bypass and $\le 10\text{ms}$ latency |
| Unit Tests | `tests/unit/services/test_workflow_registry.py` | 13 unit tests: registration, normalization, strict matching, isolation |
| Unit Tests | `tests/unit/domain/test_enums.py` | Tests verifying distinct RouteType values and wire round-trip |
| Unit Tests | `tests/unit/domain/test_route.py` | 11 unit tests verifying RouteDecision model invariants and validations |
| Unit Tests | `tests/unit/domain/test_workflow.py` | 11 unit tests verifying workflow model semantics |

---

## 4. Verification Results

- **P15 Dedicated Tests**: **61 passed, 0 failed** across all P15 modules:
  - `tests/unit/services/test_triage.py`: 18 passed
  - `tests/unit/services/test_workflow_registry.py`: 13 passed
  - `tests/unit/services/test_triage_matrix_200.py`: 2 passed (200+ test queries evaluated)
  - `tests/unit/harness/test_dispatch.py`: 6 passed
  - `tests/unit/domain/test_route.py`: 11 passed
  - `tests/unit/domain/test_workflow.py`: 11 passed
- **Full Unit Test Suite**: **819 passed, 0 failed** across the entire repository in 46.2s.
- **Coverage**:
  - `app/services/triage.py`: **97%** (spec requirement $\ge 85\%$).
  - `app/services/triage_rules.py`: **100%**.
  - `app/services/workflow_registry.py`: **96%** (spec requirement $\ge 85\%$).
  - `app/harness/dispatch.py`: **96%**.
  - `app/harness/workflows/meeting_followup.py`: **91%**.
  - `app/harness/workflows/document_briefing.py`: **87%**.
  - `app/domain/models/route.py`: **96%**.
  - `app/domain/models/workflow.py`: **97%**.
- **Linter & Type Checker**:
  - `uv run ruff check app tests`: **All checks passed (0 errors)**.
  - `uv run pyright`: **0 errors, 0 warnings, 0 informations**.
- **Performance Benchmarks**:
  - Triage latency: **< 1.0ms average, p95 < 10.0ms** (spec target $\le 10\text{ms}$).
  - Supervisor bypass rate: **> 70%** across the Vietnamese benchmark corpus (§6.1).
  - §18A.5 Compliance: **0 framework imports** in `app/domain/` or `app/services/`. LangGraph is strictly confined to `app/harness/`.
