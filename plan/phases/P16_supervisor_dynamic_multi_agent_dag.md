> Active phase file for P16. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P16`.

# P16 — SUPERVISOR + DYNAMIC MULTI-AGENT DAG

## Objective

Add dynamic cross-domain orchestration only after specialists are proven.

## P16-00 Substrate constraints

Governing decision: ADR 0011 and `MASTER_PLAN.md` §18A.

FORBIDDEN for this phase:

```text
langgraph_supervisor / create_supervisor
create_handoff_tool or any control-transfer handoff between agents
tool-wrapped subagents as the Supervisor mechanism
```

None of these can express `depends_on` dependencies, none guarantee parallel
execution of independent specialists, and handoff transfers control between agents,
which §16 and ADR 0009 prohibit.

Required instead: the Supervisor emits a typed `ExecutionPlan`; first-party code
validates the DAG; `ExecutionPlan.get_ready_tasks()` selects each runnable batch; the
batch is dispatched through `Send`.

## P16-01 Supervisor structured plan

No unstructured plans.

## P16-02 Capability-only view

Supervisor sees agent capabilities, not low-level tools.

## P16-03 DAG validation

Check:

- valid capability
- dependencies
- cycles
- max tasks
- budget
- max delegation depth per task chain

## P16-04 Executor

Parallelize independent specialists by dispatching each `get_ready_tasks()` batch
through `Send`. Sequential execution of independent tasks is a phase failure.

## P16-05 Shared State

Agents return structured results.

Cross-domain needs surface as `CapabilityRequest` / `NeedMoreContext`, which the
executor schedules. Agents never invoke peers.

## P16-05A Structured report-back handling

The DAG executor MUST handle specialist structured signals:

```text
TaskResult.status == NEEDS_MORE_CONTEXT:
  -> executor extracts NeedMoreContext.what_is_needed
  -> schedules a new task targeting the requested capability
  -> optionally sends follow-up to the original specialist

TaskResult.status == NEEDS_APPROVAL:
  -> executor collects DelegationResult.needs_approval items
  -> surfaces them in the plan result for PolicyEngine (P18)
```

## P16-05B Delegation depth tracking in DAG

The executor MUST pass `current_depth` to each specialist dispatch
through `DelegationService` (P11-07). A Supervisor plan with nested
delegation chains exceeding `max_delegation_depth` is rejected at DAG
validation time (P16-03), not at execution time.

## P16-05C Continuable Subagent Sessions vs One-shot Workers

Support two subagent lifecycles:
1. **One-shot Worker**: Spawned for an isolated task, executes, returns structured report, and is disposed immediately.
2. **Continuable Subagent Session**: When an interaction requires multiple iterative turns (e.g. follow-up clarification, multi-step refinement), the specialist session state is retained in-memory / session store, enabling follow-up message dispatch without re-initializing full system prompt or context from scratch.

*DeepSeek Harness Reference:*
- Package: `deepseek-harness/packages/subagent/subagent/` (`src/continuation.ts`, `src/types.ts`)
- Docs: `deepseek-harness/docs/subsystems/subagent.md`

## P16-05D Tool Scoping Filter & Capability Narrowing

Enforce strict capability scoping at delegation time:
- The spawning Supervisor dynamically restricts the child's available tools using `ToolRestriction` (`toolFilter`).
- The subagent ONLY sees the narrowed subset in its prompt AND any call outside the whitelist is rejected at the middleware/registry layer ("fail loud, no silent escalation").

*DeepSeek Harness Reference:*
- Package: `deepseek-harness/packages/subagent/subagent/`, `deepseek-harness/packages/core/tools/`
- Docs: `deepseek-harness/docs/subsystems/subagent.md`

## P16-06 Bounded replan

Only when information is missing.

## P16-07 No-progress detection

Stop repeated equivalent plans.

## P16-08 Final synthesis

One final synthesis where possible.

Avoid separate evaluator LLM unless needed.

Prefer deterministic validation of:

```text
task statuses
evidence count
missing_information
```

## Required scenarios

1. simple request bypasses Supervisor
2. multi-domain open task uses Supervisor
3. independent specialists run in parallel
4. missing data triggers one replan
5. replan limit stops safely
6. budget stops runaway planning
7. specialist returns NeedMoreContext -> executor schedules follow-up
8. delegation depth limit prevents unbounded delegation chains
9. continuable subagent session receives follow-up prompt without context loss
10. subagent attempts forbidden tool call outside narrowed scope → blocked loudly

## Gate

STOP.

---
