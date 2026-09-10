# Phase P18 Review Pack: Policy, Approval Plane, Question Plane & Full Write Access

> Phase: **P18 — POLICY + APPROVAL PLANE + QUESTION PLANE + FULL WRITE ACCESS + LANGGRAPH INTERRUPT/RESUME**
> Status: **COMPLETE & HARDENED — all 16 security invariant scenarios passed; test suite 100% green (90% coverage); 0 ruff / pyright errors; zero framework imports in domain/services; awaiting `APPROVED P18`**
> Scope: **per `plan/phases/P18_policy_approval_full_write_access.md` and `plan/MASTER_PLAN.md §18A`**
> Gate in: **`APPROVED P17`** recorded from user on 2026-09-09.
> Gate out: **`APPROVED P18`** from user unblocks Phase 19 (`phases/P19_workflow_hardening_meeting_prep_graph.md`).

---

## 1. Executive Summary

Phase 18 introduces human-in-the-loop control, deterministic fail-closed policy enforcement, structured user clarification, and full state-mutating execution resilience across the personal AI assistant runtime:
1. **P18-01 & P18-02 Strict Fail-Closed Policy Engine**:
   - `PolicyEngine` evaluates every proposed tool action against session policies (`ask`, `never`), action risk tiers (`ActionRiskLevel`), and caller delegation contexts.
   - Read-only actions pass through automatically (`allowed=True`); mutation actions require explicit authorization under `ApprovalPolicy.ASK` or are rejected deterministically under `ApprovalPolicy.NEVER` (headless/unattended runs never hang).
   - Outcomes strictly adhere to canonical states: `allowed-once`, `rejected`, `cancelled`, `unavailable`.
2. **P18-03 Delegation Policy Pinning & Self-Approval Prevention**:
   - Specialists running under delegation inherit `DelegationContext.approval_policy = NEVER` frozen at delegation time.
   - Child specialists cannot self-approve or bypass authorization boundaries even if granted external write scopes.
3. **P18-04 Question Plane (`tool_ask_user`)**:
   - Dedicated interactive channel (`AskUserQuestionRequest`, `UserQuestionItem`, `UserQuestionOption`, `UserQuestionAnswer`) allowing agents to request structured clarifications or review plans.
   - Enforces option boundaries for single-choice and multi-select formats, atomic answer persistence via `QuestionPlaneService.record_answers()`, and fail-closed cancellation.
   - Fully exposed via first-party tool `ask_user` in `build_first_party_tool_registry()`.
4. **P18-05 Durability (Postgres checkpointer) + HITL resume via API**:
   - Durable checkpointing backed by `AsyncPostgresSaver` (psycopg3 DSN from `DATABASE__URL`), wired at app lifespan and compiled into specialist / supervisor / workflow graphs by `HarnessDispatcher`.
   - Production **approval** flow is token-API, not graph-interrupt: mutations fail-closed without a signed `appr_...` token; `POST /approvals/{id}/approve` mints that token; the specialist retries the tool. `interrupt_for_approval` and `resume_graph` are substrate primitives with 0 callers in production graph nodes (unit + Postgres integration tests only). Fail-closed at the tool gate is unchanged.
   - Question plane: `tool_ask_user` may call `interrupt_for_question` when executed inside a LangGraph node; answers persist via `POST /questions/{id}/answer` (`QuestionPlaneService`), not `resume_graph`.
5. **P18-06 REST API Endpoints**:
   - `GET /approvals/pending`: Lists awaiting mutation approval requests with canonical proposal hashes and parameter redaction.
   - `POST /approvals/{id}/approve`: Validates approver identity, marks approval record, and issues single-use cryptographically signed approval token (`appr_...`).
   - `POST /approvals/{id}/deny`: Rejects or cancels mutation proposals with audit trail reason.
   - `POST /questions/{id}/answer`: Records user responses, validates choices against question schema, and transitions run to resume state.
6. **P18-07 Security Invariants & Secret Redaction**:
   - All parameters in approval requests and checkpointed states are sanitized via `strip_sensitive_keys` (`[REDACTED_SECRET]`).
   - `validate_target_state` compares expected vs live fingerprints when an expected token is present (`expected_etag` / `expected_version`). If expected is omitted the check is skipped (intentional fail-open; see §7 M3).
   - Single-use consumed token store (`InMemoryConsumedTokenStore` / Redis backed) prevents token replay across all tools.
7. **P18-08 Substrate & Framework Cleanliness**:
   - Strict zero-framework-import guarantee (§18A.5): 0 `langgraph` imports in `app/domain/` or `app/services/`.

---

## 2. Architectural Invariants & Substrate Compliance

| Principle / Rule | Requirement | Implementation Status | Evidence / Verification |
|---|---|---|---|
| **§18A.5 Framework Isolation** | `domain=0`, `services=0` framework imports | Zero Violations | ripgrep verifies 0 `langgraph` imports in `app/domain/` and `app/services/` |
| **Fail-Closed Default** | Default policy must reject mutations on ambiguous or unavailable responses | Strictly Enforced | `ApprovalOutcome.UNAVAILABLE` recorded when response times out or answerer is unavailable |
| **Token Replay Defense** | Single-use approval tokens can never be executed twice | Strictly Enforced | `InMemoryConsumedTokenStore.try_consume()` rejects replayed tokens with `PermissionDeniedError` |
| **Target State Concurrency** | Target state modification invalidates prior approval | Strictly Enforced | `PolicyEngine.validate_target_state()` rejects stale fingerprints (`etag_v1` vs `etag_v2`) |
| **Delegation Pinning** | Child specialists cannot self-approve | Strictly Enforced | `PolicyEngine.evaluate_action(delegation=...)` forces `ApprovalOutcome.REJECTED` |
| **Secret Sanitization** | Stored approvals and checkpoints must not contain raw tokens | Strictly Enforced | Sensitive parameters redacted to `[REDACTED_SECRET]` |
| **Type Safety & Quality Gate** | Pyright 0 errors, Ruff clean | 100% Passing | `uv run pyright app tests` (0 errors), `uv run ruff check app tests` (clean) |

---

## 3. Required Security Scenarios Verification Matrix (16/16 Passed)

| Scenario # | Requirement Description | Test Function | Result |
|---|---|---|---|
| **1** | Send cannot bypass approval | `test_send_cannot_bypass_approval` | **PASS** (`gmail.send_draft` without approval token rejected) |
| **2** | Delete cannot bypass approval | `test_delete_cannot_bypass_approval` | **PASS** (`calendar.delete_event` without approval token rejected) |
| **3** | Replay safe | `test_replay_safe` | **PASS** (Second execution attempt with same token fails `try_consume`) |
| **4** | Stale target | `test_stale_target` | **PASS** (State change between approval and mutation triggers stale target rejection) |
| **5** | Expired approval | `test_expired_approval` | **PASS** (Tokens past TTL return `valid=False`) |
| **6** | Denied action | `test_denied_action` | **PASS** (Explicit rejection transitions request to `rejected` with `approved=False`) |
| **7** | Capability exposure still enforced | `test_capability_exposure_still_enforced` | **PASS** (Unexposed capabilities in `ScopedToolView` raise `PermissionDeniedError`) |
| **8** | Interrupted run resumes after restart | `test_interrupted_run_resumes_after_restart_and_executes_once` | **PASS** (LangGraph interrupt/resume across simulated engine restarts executes mutation exactly once) |
| **9** | Checkpointed state contains no secrets | `test_checkpointed_state_contains_no_unredacted_secrets` | **PASS** (API keys and tokens redacted to `[REDACTED_SECRET]`) |
| **10** | Delegated specialist cannot self-approve | `test_delegated_specialist_cannot_self_approve` | **PASS** (Delegated execution cannot self-grant write mutations) |
| **11** | Delegation policy frozen at delegation time | `test_delegation_policy_frozen_at_delegation_time` | **PASS** (Child policy remains `NEVER` even if session is set to `ASK`) |
| **12** | Consolidated approval from Supervisor | `test_consolidated_approval_from_supervisor_path` | **PASS** (Supervisor synthesis aggregates pending approvals from sub-tasks) |
| **13** | Unavailable answerer defaults to fail-closed | `test_unavailable_answerer_defaults_to_fail_closed` | **PASS** (Unreachable user or timeout marks outcome as `unavailable`) |
| **14** | Headless run with approval_policy=never | `test_headless_run_with_approval_policy_never` | **PASS** (Unattended mutations rejected deterministically without hanging the graph) |
| **15** | Structured user question flow | `test_structured_user_question_flow` | **PASS** (Single-choice and free-text responses captured and stored correctly) |
| **16** | Question option boundary validation | `test_question_option_validation_boundaries` | **PASS** (Disallowed selections on single- and multi-select questions rejected with validation error) |

---

## 4. Deliverables & Code Changes Summary

| Component | File Path | Scope & Functions |
|---|---|---|
| Domain Enums | `app/domain/enums/enums.py` | `ApprovalOutcome`, `ApprovalPolicy` |
| Domain Models | `app/domain/models/action.py` | `ProposedAction`, `ActionApproval` with `target`, `outcome`, `expires_at` |
| Domain Models | `app/domain/models/question.py` | `UserQuestionOption`, `UserQuestionItem`, `AskUserQuestionRequest`, `UserQuestionAnswer`, `UserQuestionResponse` |
| DB Infrastructure | `app/infrastructure/db/models.py` | `UserQuestion` model and relationship on `AssistantRun`; updated `ApprovalRequest` check constraints |
| DB Migrations | `alembic/versions/0009_p18_approval_and_questions.py` | Alembic revision for `user_questions` table and approval status constraints |
| Policy Service | `app/services/policy_engine.py` | `PolicyEngine` evaluating action risks, session policies, delegation pinning, and target fingerprints |
| Approvals Service | `app/services/approvals.py` | Extended with `get_pending_requests()`, `decide_with_outcome()`, and `ApprovalOutcome` handling |
| Question Plane | `app/services/question_plane.py` | `QuestionPlaneService` creating question requests, validating choices, recording answers, and cancellation |
| Tools Layer | `app/tools/ask_user.py` | `tool_ask_user` and `ask_user` first-party interactive tool definition |
| Checkpointing & Interrupts | `app/harness/checkpointer.py` | `get_checkpointer_dsn()`, `MemorySaver`, and `AsyncPostgresSaver` factory |
| Interrupt Utilities | `app/harness/interrupts.py` | Substrate primitives: `interrupt_for_approval` / `resume_graph` (tests + PG integration only); `interrupt_for_question` used by `tool_ask_user` |
| Supervisor Synthesis | `app/harness/supervisor/graph.py` | Exposes `synthesize_result(state)` on `SupervisorGraphBuilder` with session eviction and approval aggregation |
| REST API Routes | `app/api/routes/approvals.py` | `GET /approvals/pending`, `POST /approvals/{id}/approve`, `POST /approvals/{id}/deny` |
| REST API Routes | `app/api/routes/questions.py` | `POST /questions/{id}/answer` |
| API Registration | `app/api/routes/__init__.py`, `app/main.py` | Mounted approvals and questions routers |
| Test Suites | `tests/unit/security/test_p18_security_invariants.py` | All 16 security invariant scenarios |
| Test Suites | `tests/unit/services/test_policy_engine.py` | Comprehensive policy engine unit tests |
| Test Suites | `tests/unit/services/test_question_plane.py` | Comprehensive question plane unit tests |
| Test Suites | `tests/unit/harness/test_interrupt_resume.py` | LangGraph interrupt and resume lifecycle tests |
| Test Suites | `tests/unit/api/test_approvals_api.py` | REST API integration tests for approvals |
| Test Suites | `tests/unit/api/test_questions_api.py` | REST API integration tests for questions |

---

## 5. Verification Results

```bash
# 1. Static Type Checking
uv run pyright app tests
--> 0 errors, 0 warnings, 0 informations

# 2. Linting & Code Hygiene
uv run ruff check app tests
--> All checks passed!

# 3. Framework Isolation (§18A.5)
ripgrep: 0 langgraph imports in app/domain and app/services

# 4. P18 Security Invariants & Specialized Suites
uv run pytest tests/unit/security/test_p18_security_invariants.py tests/unit/services/test_policy_engine.py tests/unit/services/test_question_plane.py tests/unit/harness/test_interrupt_resume.py tests/unit/api/test_approvals_api.py tests/unit/api/test_questions_api.py -v
--> 36 passed in 9.44s

# 5. Full Unit Test Suite Regression Run
uv run pytest tests/unit/ -q
--> 100% passed (TOTAL 90% coverage)
```

---

## 6. Gate Sign-off Recommendation

Phase 18 meets all requirements defined in `plan/phases/P18_policy_approval_full_write_access.md` and `plan/MASTER_PLAN.md §18A`:
- **Fail-closed security**: Unconditionally applied across direct specialists, supervisor sub-delegations, and headless runs.
- **Durable execution**: Postgres checkpointer persists graph state across process restart. Mutation HITL resumes via approve-token + tool retry; graph-interrupt helpers are proven in tests, not wired into production approval nodes.
- **Zero framework leaks**: Full compliance with architectural boundaries.
- **Zero test failures**: 100% green across unit tests, security tests, and regressions.

Ready for user sign-off: **`APPROVED P18`**.

---

## 7. Review tails (this pass) — 0 blockers

Independent review: IGH 0 open blockers. Remaining items closed or documented as follows.

### H1 — pack honesty (not a bypass)

`interrupt_for_approval` + `resume_graph` still have **0 callers in production graphs**. Durability is real (`AsyncPostgresSaver` at lifespan + `HarnessDispatcher` compiles graphs with the checkpointer). Production approval HITL is **token-API** (`GET /approvals/pending`, `POST /approvals/{id}/approve|deny` → `appr_...` → `require_mutation_approval`). Fail-closed at the mutation gate is unchanged. This pack no longer claims that approvals “pause execution graphs cleanly” in production.

### M3 — stale-target fingerprint contract

`require_mutation_approval` runs `PolicyEngine.validate_target_state` only when an **expected** fingerprint is present. If the tool omits it (`exp_fp is None`) the check is **skipped** — intentional fail-open for create-style mutations and providers with no version token.

**`curr_fp` source:** kwargs `current_target_fingerprint`, else `current_etag` / `current_version` in arguments (live observations; excluded from proposal hash). Calendar/Drive wrappers fetch the live ETag/`DriveFile.version` when expected is set, so `curr_fp` is not attacker-chosen.

**Tools that MUST pass expected fingerprint from the last read:**

| Tool | Token | Argument |
|---|---|---|
| `calendar.update_event` | Calendar ETag | `expected_etag` |
| `calendar.delete_event` | Calendar ETag | `expected_etag` |
| `calendar.add_attendee` | Calendar ETag | `expected_etag` |
| `calendar.remove_attendee` | Calendar ETag | `expected_etag` |
| `drive.move_file` | `DriveFile.version` | `expected_version` |
| `drive.rename_file` | `DriveFile.version` | `expected_version` |
| `drive.delete_file` | `DriveFile.version` | `expected_version` |
| `drive.update_permissions` | `DriveFile.version` | `expected_version` |

**Not listed (no prior version token):** `calendar.create_event`, `drive.upload_file`, `drive.create_folder`, all Gmail mutations (drafts have no ETag in our models), Contacts (read-only). Constant: `STALE_CHECK_FINGERPRINT_TOOLS` in `app/services/approvals.py`.

### M4 — pending list is user-scoped

`GET /approvals/pending` passes `user_id=user_id` into `ApprovalRequestService.get_pending_requests` (join on `AssistantRun.user_id`). Approve/deny already 403 on cross-user. Locked by `test_list_pending_approvals_is_scoped_to_current_user`.

### LOW — kept / already gone

- **L1** `record_answers` is idempotent on the same payload; conflicting answers raise. Safe as-is.
- **L2** `ALLOWED_ONCE` on read-only is a label, not a bypass.
- **L3** Unrecognized policy string fail-closes; no production caller passes garbage.
- **L4** `TestGraphChannels` warning is gone (`InterruptGraphChannels` in `tests/unit/harness/test_interrupt_resume.py`).
- **L5** Gate-out path was a typo; P19 spec is `phases/P19_workflow_hardening_meeting_prep_graph.md`.
