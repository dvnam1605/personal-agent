> Active phase file for P0. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P0`.

# P0 — ARCHITECTURE CONTRACT + EXECUTION PRINCIPLES

## Objective

Freeze the V2 architecture before business implementation.

## P0-01 — Scope contract

Create:

```text
docs/architecture/v1-scope.md
```

Must include:

- V1 features
- non-goals
- supported integrations
- supported read/write capabilities
- agent inventory
- service inventory
- scaling assumptions

## P0-02 — Execution-path ADR

Document:

```text
DIRECT SPECIALIST
KNOWN WORKFLOW
SUPERVISOR
```

Include decision criteria and examples.

## P0-03 — Agent-boundary ADR

Define responsibilities for:

```text
SupervisorAgent
CommunicationAgent
CalendarAgent
KnowledgeResearchAgent
```

## P0-04 — Loop-vs-graph ADR

Define:

```text
when loop is allowed
when graph is preferred
when a loop should be hardened into a graph
```

## P0-05 — Skills-to-Graph ADR

Define lifecycle:

```text
Procedure idea
 -> Skill
 -> traces/evaluation
 -> stable?
 -> Static Graph
```

Specify graph-hardening evidence requirements.

## P0-06 — Capability-gating ADR

Specify which tool categories each agent may see.

## P0-07 — Memory-gate ADR

Define when memory retrieval occurs and when it does not.

## P0-08 — LLM-budget ADR

Define engineering rule:

```text
No LLM call without a reason.
```

Include route-specific budgets.

## P0-09 — Agent communication ADR

Explicitly prohibit free peer-to-peer agent calls.

## P0-10 — Safety/action policy ADR

Define canonical action classes.

## P0-11 — Canonical architecture diagram

Produce the architecture from this plan as project documentation.

## P0 Deliverables

```text
docs/architecture/v1-scope.md
docs/architecture/execution-paths.md
docs/architecture/agent-boundaries.md
docs/architecture/loop-vs-graph.md
docs/architecture/skills-and-graphs.md
docs/architecture/capability-gating.md
docs/architecture/memory-gate.md
docs/architecture/latency-budget.md
docs/architecture/action-policy.md
docs/architecture/architecture.md
docs/adr/...
```

## P0 DoD

Must be unambiguous:

- 4 agent roles
- 3 execution paths
- direct vs ReAct rules
- Supervisor activation condition
- Skills lifecycle
- Graph hardening condition
- Capability gating
- Memory gating
- latency/LLM budget
- action safety

## P0 Forbidden

No real integration/business implementation.

## P0 Gate

```text
WAITING FOR USER REVIEW — P0
```

Only:

```text
APPROVED P0
```

unlocks P1.

---
