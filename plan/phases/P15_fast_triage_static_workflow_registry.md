> Active phase file for P15. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P15`.

# P15 — FAST TRIAGE + STATIC WORKFLOW REGISTRY

## Objective

Route simple requests efficiently and establish graph infrastructure.

## P15-01 Fast triage

Use:

```text
deterministic first
known workflow match
small classifier if needed
```

## P15-02 RouteDecision

Must produce canonical structured result.

## P15-03 Known Workflow Registry

Implement:

```text
register
match
execute
```

No large workflow required yet.

## P15-04 Graph runtime

Build on `StateGraph` using the channel schema established in P11-00. Do not write a
bespoke node executor.

Support nodes that may be:

```text
deterministic function
tool operation
specialist execution
LLM synthesis
```

Route dispatch uses conditional edges. The route decision itself stays first-party
(`FastTriage` / `RouteDecision`); the graph only carries it out.

## P15-05 Parallel execution

Support independent nodes via the `Send` API.

Scheduling authority stays first-party: the registry/plan selects which nodes are
runnable, and `Send` only dispatches that batch.

## P15-06 Fast path tests

Examples:

```text
"Lịch mai?"
 -> CalendarAgent
 -> NO Supervisor

"Email Nam?"
 -> CommunicationAgent
 -> NO Supervisor
```

## P15-07 Budget validation

Assert unnecessary Supervisor use in simple evals is a failure.

## Gate

STOP.

---
