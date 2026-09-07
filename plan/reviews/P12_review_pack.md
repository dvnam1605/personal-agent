# Phase P12 Review Pack: CommunicationAgent + CalendarAgent

> Phase: **P12 — COMMUNICATIONAGENT + CALENDARAGENT**
> Status: **IMPLEMENTED, TESTED, AND VERIFIED GREEN**
> Scope: **P12A + P12B per `phases/P12_communicationagent_calendaragent.md`**
> Gate in: **APPROVED P11** received from user 2026-09-07 ("appoived p11").

---

## 1. Executive Summary

Phase 12 builds the first two production domain specialists on the P11
runtime. No new framework was introduced: both agents are thin deterministic
domain layers (system preambles, `SpecialistTask` builders, disambiguation
reports, `ProposedAction` builders) executed by the unchanged
`SpecialistRunner` + `ModeSelector` + `CapabilityGate`.

```text
latest_email_task / schedule_query_task  ──► mode=DIRECT ──► 1 LLM call, 0 steps
complex_task / slot_search_task          ──► BOUNDED_REACT (budget: ≤4 steps, ≤3000 prompt tokens)
ambiguous contact                        ──► NEEDS_MORE_CONTEXT + candidates (never guess)
send / booking intent                    ──► ProposedAction(HIGH_IMPACT_WRITE, requires_approval=True)
live mutation without approval           ──► POLICY stop, NEEDS_APPROVAL, executor never called
```

---

## 2. Deliverables by Spec Item

| Spec | Artifact | Notes |
|---|---|---|
| P12 entry (declarations) | `app/agents/declarations.py`, `app/agents/__init__.py` | `COMMUNICATION_AGENT` (`COMMUNICATION`, categories `["gmail","contacts"]`, caps `["gmail.*","contacts.*"]`) and `CALENDAR_AGENT` (`CALENDAR`, `["calendar"]`, `["calendar.*"]`); both `BOUNDED_REACT` default, delegation allowed, depth 3; `build_first_party_registry()` preloads both |
| P12 §3.1 visibility | Declarations + gate (no new code) | All spec tool names verified present in `COMMUNICATION_TOOL_DEFINITIONS` / `CALENDAR_TOOL_DEFINITIONS`; category + hierarchical capability match (`gmail.*` ⊇ `gmail.read`) |
| P12A §3.2/§3.3 | `app/agents/specialist/communication.py` | Preamble (5 rules); `latest_email_task` / `thread_read_task` / `contact_lookup_task` (DIRECT); `complex_task` (ReAct, P12 budget); `disambiguation_report` (multi → list candidates, zero → ask manual email); `build_send_proposal` (sanitized preview via `sanitize_string`, exact recipients in `parameters["to"]`, `tool_name="gmail.send_draft"`) |
| P12B §4.1/§4.2 | `app/agents/specialist/calendar.py` | Preamble (5 rules incl. never-compute-slots); `schedule_query_task` (DIRECT, ISO-8601 + `time_max > time_min` validated); `slot_search_task` (ReAct, goal pins `duration_minutes`, UTC window, `08:30–17:30`, `preferred_time_of_day`, `Asia/Ho_Chi_Minh`); `build_event_proposal` (`tool_name="calendar.create_event"`); `no_availability_report` (SUCCESS without guessing) |
| P12 §5 fail-closed | Runner behavior (P11, asserted here) | `permit_mutations=False` default → mutation call terminates `POLICY`/`NEEDS_APPROVAL` before the executor; tool wrappers independently raise `PermissionDeniedError` without `approval_token` (P06/P07/H3) |
| P12 §6.1 unit | `tests/unit/agents/test_agent_declarations.py` (4), `test_communication_agent.py` (7), `test_calendar_agent.py` (6) | All 7 named spec tests + declaration/gating (4) + ReAct-budget efficiency + validation tests |
| P12 §6.2 integration | `tests/integration/test_communication_calendar_collab.py` | Email → slot search (read-only view) → draft-invitation proposal; asserts call order `get_free_busy → find_free_slots`, `needs_approval`, proposal payload |

No LLM calls, no network, no secrets in any new code or test
(`ScriptedChat` + `DictExecutor` only).

---

## 3. Spec-vs-Code Mappings (decided, not deferred)

1. `SpecialistResult(status="NEEDS_INPUT")` (§5) does not exist in P11.
   Mapped to `SpecialistReport(status=NEEDS_MORE_CONTEXT, missing_context=[...])`
   for contact-not-found/ambiguous; `NEEDS_APPROVAL` for mutations.
   The string `NEEDS_INPUT` appears nowhere in code.
2. "`RepeatGuard` halts after 5" (§5) vs P11 `repeat_breaker_after=3`:
   P11 constant wins; tests assert `react_steps <= 4` via the P12 task budget
   (`max_react_steps=4`), no new hard-coded threshold.
3. `LatencyBudgetManager` (§1) has no such class in P11; the equivalent is
   `ExecutionBudget` + runner pre/post-step stops. P12 tasks pin
   `max_react_steps=4, max_prompt_tokens=3000, max_total_tokens=4000`.
4. "Direct Mode: 0 or 1 LLM call" (§3.2): DIRECT consumes exactly 1 LLM turn;
   "0" is satisfied at the task-selection level (no ReAct iterations,
   `react_steps == 0`, `escalated_to_react is False`), asserted in both
   direct tests.

---

## 4. Verification

- New tests: **18 passed** (4 declarations + 7 communication + 6 calendar + 1 integration).
- Full suite: **718 passed, 0 failed, 0 errors, 0 skipped** — includes the
  10 live-PostgreSQL tests on `localhost:5434`, which execute for the first
  time in this working tree (previously skipped: PG unreachable).
- Coverage on new modules: `declarations.py` **100%**,
  `specialist/calendar.py` **96%**, `specialist/communication.py` **91%**
  (all ≥ 85% per §7.4).
- `ruff check app tests`: **pass**. `ruff format --check` on all 10 new/touched
  files: **clean** (remaining `react.py` finding is pre-existing, untouched).
- `pyright` on the 3 new app files: **0 errors, 0 warnings**.
- Pass criteria §7: (1) 3 direct patterns ≤1 LLM call ✓ (tested: latest-email,
  tomorrow-schedule + contact-lookup builder covered by validation/direct
  tests); (2) ReAct ≤4 iters / ≤3000 prompt tokens ✓ (both complex tests);
  (3) 100% mutation fail-closed ✓ (both blocked tests assert
  `executor.calls == []`); (4) coverage ≥85% ✓; (5) no regressions ✓
  (718/718 green).

---

## 4.1. Pre-existing Defects Found & Fixed During P12 Verification

Live PG became reachable mid-session, unmasking two dormant P10A defects
(both masked while PG was down). Fixed minimally; no P10A behavior changed:

1. **Migration 0007 invalid DDL** (`alembic/versions/0007_add_fts_to_document_chunks.py`):
   `CREATE TEXT SEARCH CONFIGURATION IF NOT EXISTS` is a syntax error on every
   PostgreSQL version, so `upgrade head` could never succeed against live PG.
   Replaced with an idempotent `DO $$ ... IF NOT EXISTS (pg_ts_config) ...`
   block (no DROP/CASCADE — dependents in other schemas are preserved).
2. **Stale head pin** (`tests/integration/test_postgres_persistence.py:69`):
   asserted `version_num == "0006"` although P10A added 0007 (FTS) + 0008
   (HNSW). Updated to `"0008"` and extended the contract (vietnamese_simple
   config, `search_vector` column, HNSW index — all schema-scoped via
   `current_schema()` because the shared `public` schema carries leftovers
   from older live-PG sessions). Environment data left untouched.

---

## 4.2. Live Verification Against Real Google APIs (2026-09-07)

With the user's OAuth consent (all 4 scopes granted, tokens persisted
encrypted), the P12 tool surfaces were exercised live (read-only):

- `gmail.search_messages("in:inbox")` → **success, 5 messages** (account holds
  12,648 messages; search returns metadata summaries as designed).
- `calendar.list_events` (Aug–Sep + full 2026 windows) → **success, 0 events**;
  raw `events.list` confirms the primary calendar is genuinely empty.
- `contacts.search` → **initially failed (HTTP 404)**: root cause was a
  pre-existing P06 defect — `ContactsAdapter` called
  `https://www.googleapis.com/v1/people:searchContacts`, but the People API is
  not served from the universal host (verified: 404 there, 200 on
  `people.googleapis.com`). Fixed by routing both Contacts calls through
  absolute `PEOPLE_API_BASE_URL` URLs
  (`app/integrations/google_contacts.py`). Post-fix live result: **success,
  empty page** (no "nam" contact — correctly flows to `disambiguation_report`).
  Existing URL-suffix unit assertions still pass (86 related tests green).
- A stale grant from 2026-08-19 (Testing-mode 7-day refresh expiry) was
  replaced by the fresh consent; refresh + encrypted persistence verified.

---

## 5. Out of Scope / Deferred (unchanged)

- `ApprovalService` wiring of proposals into live execution (P16/P18 HITL).
- `KnowledgeResearchAgent` (P13), skills (P14), triage/static workflows (P15).
  (Note: live Gmail/Calendar/Contacts reads were verified in §4.2 above.)

---

## 6. Gate Request

P12 is complete per §7. **STOP — awaiting `APPROVED P12` before any P13 work.**
Next spec on approval: `phases/P13_knowledgeresearchagent.md`.
