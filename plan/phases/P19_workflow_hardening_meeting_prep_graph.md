> Active phase file for P19. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P19`.

# P19 — WORKFLOW HARDENING + MEETING PREP GRAPH

## Objective

Apply the Skill -> Graph lifecycle.

MeetingPrep Skill from P14 is evaluated.

## P19-01 Review historical traces

Analyze:

```text
execution shapes
common agent sequence
parallel opportunities
deviations
latency
LLM-call count
```

## P19-02 Graph hardening decision

Only proceed if evidence shows the workflow is stable.

If not stable:

```text
do NOT force graph
continue using Skill
document why
```

## P19-03 MeetingPrep Graph

Likely shape:

```text
Find meeting
     |
     v
Resolve context
     |
 +---+-------------------+
 |                       |
 v                       v
Communication        KnowledgeResearch
 |                       |
 +-----------+-----------+
             |
             v
          Synthesis
```

Potential Contact resolution may be deterministic/shared.

## P19-04 Parallelism benchmark

Compare:

```text
Skill dynamic execution
vs
Static Graph
```

Measure:

```text
latency
LLM calls
tool calls
answer quality
failure rate
```

## P19-05 Graph acceptance

Graph is accepted only if it provides clear benefits or predictability without quality regression.

## Gate

STOP.

---
