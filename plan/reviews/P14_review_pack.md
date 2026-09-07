# Phase P14 Review Pack: Skill System + First Dynamic Skills

> Phase: **P14 — SKILL SYSTEM + FIRST DYNAMIC SKILLS**
> Status: **APPROVED & CLOSED 2026-09-07**
> Scope: **per `phases/P14_skill_system_first_dynamic_skills.md`**
> Gate in: **APPROVED P13** received from user 2026-09-07 ("approved p13").
> Gate out: **APPROVED P14** received from user 2026-09-07 ("approved p14").

---

## 1. Executive Summary

Phase 14 adds the dynamic skill substrate: declarative markdown+YAML procedures
that specialists (and a future Supervisor) can discover, gate, and follow
without compiling LangGraph workflows. Skills are guidance documents. Execution
authority stays in the P11 runner, capability gate, and mutation policy.

```text
SKILL.md + metadata.yaml
        │  yaml.safe_load only — never exec/eval
        v
SkillRegistry.register / load_from_directory / match(intent, caps)
        │  latest semver; unaccented trigger + capability intersection
        v
SkillExecutor.activate(...)
        │  missing caps / read-only mutation still fail closed
        │  mutation skills activate read steps without approval_token
        v
intersected ToolRegistryView + SpecialistTask.system_preamble
        │  SpecialistRunner applies task.tool_restriction
        │  mutation tool → NEEDS_APPROVAL until token is present
        v
SkillExecutor.observe_run(outcome) → ADR 0005 observed tool path
```

Proof required by the execution card: editing `SKILL.md` changes the parsed
procedure without changing runtime code (tested).

---

## 2. Deliverables by Spec Item

| Spec | Artifact | Notes |
|---|---|---|
| P14 §3.1 contract | `app/domain/models/skill.py` | `SkillMetadata` / 1-based `SkillStep` / `SkillDefinition`; P2 `SkillConstraints` + `SkillCompletionCriteria` kept as supporting types |
| P14 §3.2 registry | `app/services/skills/registry.py` | `register`, `get(name, version?)`, `match`, `load_from_directory`, `list_all`; latest-semver default |
| Loader | `app/services/skills/loader.py` | `SKILL.md` step headings + optional `metadata.yaml` / frontmatter; `yaml.safe_load` only |
| P14 §3.3 meeting-prep | `skills/meeting-prep/` | 6 ordered read-only steps; calendar + gmail + drive + retrieval |
| P14 §3.3 email-follow-up | `skills/email-follow-up/` | 4 steps; `gmail.create_draft` is a mutation (`is_mutation=true`) |
| §4 gate intersection | `app/services/skills/execution.py` | Agent view ∩ skill required capabilities ∩ `allowed_tools`; mutations stripped unless approved |
| §4 no code eval | loader + registry | `.py` files in a skill folder are rejected; markdown is never executed |
| Execution card: guidance | `build_skill_preamble` + `SpecialistTask` | Procedural preamble only; no new StateGraph |
| Execution card: telemetry | `app/services/skills/telemetry.py` | In-memory activations, unique step sequences, `ready_for_hardening` |
| P14 §5 tests | `test_skill_registry.py`, `test_skill_definitions.py`, `test_skill_execution.py`, updated `test_skill.py` | All named spec cases plus loader/safety edges |

No LLM calls, no network, no secrets in any new code or test.

---

## 3. Spec-vs-Code Mappings (decided, not deferred)

1. **P2 vs P14 `SkillDefinition`:** P14 is the production contract. P2's
   0-based `SkillStep` / flat `name`+`category` shape is replaced. Supporting
   P2 types remain. Domain tests were rewritten to the P14 model.
2. **`step_index` is 1-based** per the P14 snippet (`Field(ge=1)`), not P2's
   0-based sequence.
3. **`SkillStep.tool_name`:** extra optional field so execution-safety tests
   can bind a step to a real registry tool (`gmail.create_draft`, etc.).
4. **`SkillDefinition.guidance`:** verbatim `SKILL.md` body injected as
   untrusted preamble text (skills-as-documents, not a second prompt channel
   with extra authority).
5. **Plan aliases vs registered tools:** `gmail.list_messages` →
   `gmail.search_messages`; `rag.search` → `retrieval.retrieve` /
   `retrieval.synthesize`. Documented in the skill markdown.
6. **`LatencyBudgetManager` / live runner wiring:** same P11 mapping as
   P12/P13 — `SkillExecutor` emits a `SpecialistTask`; it does not call
   `SpecialistRunner`. Chat/session wiring remains P15+.
7. **Graph-candidate stats** are in-process (`SkillTelemetry`), not the
   unused `skills` SQL table. Persistence can wait for evaluation (P20) /
   hardening (P19).

---

## 4. Verification

- New/updated tests: **47** skill tests (10 domain + 14 registry + 8
  definitions + 15 execution) plus 1 new `SpecialistRunner` restriction test.
- Full suite: **785 passed, 0 failed, 0 errors** (includes live-PG tests).
- Coverage on new/touched skill modules: `skill.py` **100%**,
  `services/skills/*` **100%** (`__init__`, `execution`, `loader`, `matching`,
  `registry`, `telemetry`).
- `ruff check app tests`: **pass**. `ruff format --check` on touched files:
  **clean**.
- `pyright` on new/touched app + test files: **0 errors**.
- Pass criteria §5: (1) schema/version/missing-steps rejected ✓;
  (2) trigger match + capability filter (including unaccented Vietnamese) ✓;
  (3) meeting-prep parses 6 ordered steps ✓; (4) email-follow-up parses ✓;
  (5) mutation steps cannot execute in a read-only view or without
  `approval_token` (`NEEDS_APPROVAL` after read steps) ✓.

---

## 5. Out of Scope / Deferred (unchanged)

- Fast Triage selecting skills (P15).
- Supervisor consuming the skill catalog (P16).
- Compiling `meeting-prep` into `MeetingPrepGraph` (P19; ADR 0005 still
  requires ≥50 traces at ≥90% structural stability).
- Persisting skill runs / graph-candidate stats to Postgres.
- Calling `SpecialistRunner` from `SkillExecutor` (chat API / P16+).
- Real web-search provider (still P13 mock seam).

---

## 6. Architecture decisions

- Skills are data, not code: markdown + YAML, `yaml.safe_load`, no `exec`.
- Effective tools = intersection of the caller's gated view and the skill's
  declared capabilities/allowlist. A skill cannot grant tools the agent
  lacks.
- Mutation skills (`email-follow-up`) cannot activate in an intrinsically
  read-only view. They **may** activate without `approval_token` /
  `permit_mutations` so read steps can run; `gmail.create_draft` then
  returns `NEEDS_APPROVAL` from the specialist runtime. Both flags plus a
  token are required to *arm* the mutation.
- `SkillTelemetry.record_outcome` records the observed ReAct tool path after
  `SpecialistRunner.run()`. `activate()` does not write traces.
- No static graph in this phase (MASTER_PLAN §13 / ADR 0005).

---

## 7. Public contracts changed

- `SkillDefinition` / `SkillStep` now match P14 (breaking vs the unused P2
  placeholder shape). Callers: domain tests only.
- New exports: `SkillMetadata`, `SkillRegistry`, `SkillExecutor`,
  `load_production_skills`.

## 8. Database migrations

None. Existing `skills` table is unused by this phase.

## 9. Tests added

- `tests/unit/domain/test_skill.py` (rewritten)
- `tests/unit/services/test_skill_registry.py`
- `tests/unit/services/test_skill_definitions.py`
- `tests/unit/services/test_skill_execution.py`

## 10. Test results

```text
uv run pytest
790 passed, 0 failed, 0 errors
uv run ruff check app tests          pass
uv run ruff format --check (touched) clean
uv run pyright (touched)             0 errors
```

## 11. Manual verification

Not required. Production skills load from `skills/` in unit tests.
No live Google / LLM invocation.

## 12. LLM-call/latency observations

None. No LLM calls.

## 13. Security/privacy notes

- Skill markdown is treated as untrusted guidance text in the preamble.
- Python files inside a skill directory are rejected.
- Mutation draft creation still requires approval; send/reply/forward are
  not in the email-follow-up allowlist.

## 14. Known limitations

- `SkillTelemetry` is process-local and only meaningful after
  `observe_run` / `record_outcome` (P16+ chat wiring still deferred).
- No HTTP/chat path activates a skill yet.
- Meeting-prep needs a caller whose capability set spans calendar + gmail +
  drive + retrieval. No single P12/P13 specialist holds all four;
  Supervisor (P16) or `MeetingPrepGraph` (P19) is the intended activator.
  Documented in `skills/meeting-prep/` and
  `docs/architecture/skills-and-graphs.md`.

## 15. Deferred items

See §5.

## 16. Diff summary

Created: `app/services/skills/` (6 modules), `skills/meeting-prep/`,
`skills/email-follow-up/`, three service test modules, this review pack.

Modified: `app/domain/models/skill.py`, `app/domain/models/__init__.py`,
`app/services/__init__.py`, `tests/unit/domain/test_skill.py`,
`plan/CURRENT_PHASE.md` (P13 closed, P14 in progress), P14 spec entry
checkboxes.

## 16.1 Post-review fixes (2026-09-07 review findings)

- **H1 (SkillTelemetry)** — `activate()` no longer records a fake completed
  declared-step sequence. Callers record after `SpecialistRunner.run()` via
  `SkillExecutor.observe_run` / `SkillTelemetry.record_outcome`, using the
  actual `SpecialistOutcome.trace` tool path. `ready_for_hardening` can no
  longer become true from activation-only loops.
- **H2 (mutation deadlock)** — `email-follow-up` activates without an
  approval token so read/reasoning steps can produce a proposal. Mutation
  tools stay in the view (so the runner can return `NEEDS_APPROVAL` instead
  of "not available") but `task.permit_mutations` stays false until both
  `permit_mutations` and a non-blank token are supplied. Intrinsically
  read-only views still fail closed at activate.
- **M1 (tool_restriction)** — `SpecialistRunner.run()` now applies
  `task.tool_restriction` (same posture as `app/harness/graph.py`).
  Restricted tools surface as "not available" observations (`NotFoundError`
  and `PermissionDeniedError` from `ScopedToolView`).
- **M2 (Vietnamese triggers)** — `trigger_matches` folds diacritics
  (`unaccent_vietnamese`, including `đ`→`d`) before substring match, then
  falls back to token-subset matching for filler variants
  (`chuan bi cuoc hop mai`).
- **M3 (dual mutation flags)** — if `constraints.allow_mutations` is not
  set explicitly, it inherits `metadata.is_mutation`. Explicit disagreement
  still fails.
- **L1 (YAML-only steps)** — `_merge_steps` raises `ValidationError` when
  YAML declares step indices missing from `SKILL.md`.
- **L2 (meeting-prep domain span)** — documented in the skill files and
  `docs/architecture/skills-and-graphs.md`; not a code defect.

## 17. Suggested reviewer focus

- Mutation fail-closed paths: activate allows reads; mutation execute still
  needs approval (`authorize_skill_step` / runner `NEEDS_APPROVAL`).
- Capability intersection cannot widen an agent view; runner restriction
  cannot be bypassed by passing a wider `tools` argument.
- ADR 0005 stats come from observed traces, not declared `SKILL.md` steps.
- Production SKILL.md tool names match the live tool registry.

## 18. Gate status: APPROVED & CLOSED

**GATE STATUS: APPROVED — P14 (2026-09-07)**

`APPROVED P14` received from user 2026-09-07 ("approved p14").
Phase 14 is closed.
Next spec active: `phases/P15_fast_triage_static_workflow_registry.md`.
