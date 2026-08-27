> Active phase file for P14. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P14`.

# P14 — SKILL SYSTEM + FIRST DYNAMIC SKILLS

## Objective

Introduce reusable procedures before hard-coded graphs.

## P14-01 SkillRegistry

Support:

```text
register
get
match
version
```

## P14-02 Skill format

Example:

```text
skills/meeting-prep/SKILL.md
```

Must describe:

```text
objective
inputs
procedure
allowed capabilities
completion criteria
output structure
safety constraints
```

## P14-03 MeetingPrep Skill

Create initial dynamic procedure.

Example:

```text
1. find target meeting
2. identify people/topic
3. collect communication context
4. collect document context
5. identify unresolved points
6. produce meeting brief
```

Do NOT hard-code a static graph yet.

## P14-04 Skill execution

Supervisor/runtime may use Skill as execution guidance.

## P14-05 Skill telemetry

Track:

```text
skill_version
steps actually taken
agents used
latency
LLM calls
tool calls
deviations
```

## P14-06 Hardening candidate signal

A Skill becomes graph candidate only when:

- repeated enough times
- stable execution shape
- low deviation
- clear dependencies
- measurable benefit

## Gate

STOP.

---
