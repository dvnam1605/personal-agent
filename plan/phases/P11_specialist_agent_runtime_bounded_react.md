> Active phase file for P11. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P11`.

# P11 — SPECIALIST AGENT RUNTIME + BOUNDED REACT

## Objective

Implement the common runtime used by all specialists.

This phase does NOT yet implement domain prompts in full.

## P11-00 LangGraph substrate wiring

Governing decision: ADR 0011 and `MASTER_PLAN.md` §18A. This is the first phase
permitted to import `langgraph`, and only inside `app/harness/`.

Deliverables:

1. Channel schema with reducers for the runtime graph state.
2. Conversion at the harness boundary in both directions:

```text
AssistantState  <->  graph channels
```

`AssistantState` uses `extra="forbid"` and `validate_assignment=True` and MUST NOT
be handed to `StateGraph` unchanged.

3. Checkpointer DSN derivation from `DATABASE__URL`:

```text
postgresql+asyncpg://...   (SQLAlchemy / domain data access)
postgresql://...           (psycopg3 / checkpointer only)
```

Both drivers coexist by design. Do not migrate the data layer to psycopg and do not
write a bespoke checkpointer over asyncpg.

Forbidden in this phase and every later phase:

```text
langgraph_supervisor / create_supervisor
create_handoff_tool or any control-transfer handoff between agents
tool-wrapped subagents as the Supervisor mechanism
```

Stop conditions, budgets, and capability gating remain first-party code. Do not
delegate them to `langgraph.prebuilt`.

## P11-01 Direct execution contract

A specialist may execute an obvious deterministic path.

## P11-02 ReAct runtime

Implement:

```text
LLM
 -> tool call
 -> observation
 -> LLM
 -> ...
 -> final
```

with bounded limits.

## P11-03 Stop conditions

- success
- no-progress
- max steps
- max tool calls
- budget
- timeout
- policy

## P11-04 CapabilityGate integration

ReAct only sees allowed tools.

## P11-05 Trace format

Record:

```text
iteration
tool
sanitized args
observation summary
stop reason
```

Do NOT store hidden chain-of-thought.

## P11-06 Execution mode selector

Specialist receives:

```text
DIRECT
REACT
```

or can choose bounded escalation from direct to ReAct under explicit rules.

## P11-07 Delegation depth enforcement

Implement delegation depth tracking and enforcement:

```text
DelegationService.delegate(parent, specialist, task) ->
  1. resolve child_depth = parent.depth + 1
  2. if child_depth > max_delegation_depth: REJECT
  3. create scoped tool view via ToolRestriction
  4. inject DELEGATION_CONTEXT into specialist
  5. pin approval_policy = NEVER on child
  6. execute specialist
  7. collect DelegationResult (including needs_approval flags)
```

This is the single delegation entry point. Neither Supervisor nor
Workflow Executor may bypass it.

## P11-08 Delegation scope context injection

Every delegated specialist execution MUST receive the
DELEGATION_CONTEXT system message (MASTER_PLAN §8.6) informing it of its
frozen permission scope. This is injected BEFORE the specialist's
domain prompt.

## P11-09 Repeat-Tool Guard & Reminder

Detect failing tool loops:
```text
If a tool is called repeatedly with identical/similar args and returns errors/empty:
 -> Inject system reminder to LLM ("Tool X failed N times with error Y. Stop retrying with same parameters; change strategy or report blocker.")
 -> If threshold exceeded: trip Circuit Breaker, fail-safe step to avoid infinite token burn.
```

*DeepSeek Harness Reference:*
- Package: `deepseek-harness/packages/guard/repeat-tool-reminder/`, `deepseek-harness/packages/guard/timeout-policy/`
- Docs: `deepseek-harness/docs/subsystems/tools.md`

## P11-10 Structured Report Return Channel

Specialists conclude their execution by calling a dedicated `report` tool with schema validation (instead of unstructured free text):

```text
Specialist Output Schema:
  status: SUCCESS | BLOCKED | NEEDS_MORE_CONTEXT | NEEDS_APPROVAL
  summary: str
  data: dict[str, Any]
  blockers / missing_context: list[str] | None
```

*DeepSeek Harness Reference:*
- Package: `deepseek-harness/packages/subagent/tool-subagent-report/`
- Docs: `deepseek-harness/docs/subsystems/subagent.md`

## Tests

- direct path no unnecessary LLM
- ReAct multiple tools
- max steps
- no-progress
- forbidden tool unavailable
- budget exhausted
- timeout
- `AssistantState` round-trips through channel conversion without field loss
- checkpointer DSN derivation strips `+asyncpg` correctly
- no `langgraph` import exists outside `app/harness/`
- delegation depth exceeded → hard reject
- delegated specialist receives DELEGATION_CONTEXT
- delegated specialist approval_policy pinned to NEVER
- repeat-tool failure triggers guard reminder and circuit breaker
- structured report validated against expected output schema

## Gate

STOP.

---
