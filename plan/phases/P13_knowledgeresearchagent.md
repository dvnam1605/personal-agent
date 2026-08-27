> Active phase file for P13. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P13`.

# P13 — KNOWLEDGERESEARCHAGENT

## Objective

Build the most autonomous V1 specialist.

## Capabilities

```text
internal RAG
Drive search/read
web search
source comparison
evidence synthesis
```

## P13-01 Internal mode

Use internal evidence only unless user request requires external data.

## P13-02 Web mode

Preserve source class.

## P13-03 Mixed mode

Support:

```text
internal report
vs
current external information
```

## P13-04 ReAct evidence loop

Track:

```text
required evidence
found evidence
remaining gaps
```

## P13-05 Read-only capability exposure

Research runs must not receive mutation tools.

## P13-06 No-answer policy

Insufficient evidence -> PARTIAL/NEEDS_CONTEXT.

## Tests

- internal only
- web only
- mixed
- conflicting evidence
- missing evidence
- max loop
- prompt injection text inside documents/web
- no mutation tool exposure

## Gate

STOP.

---
