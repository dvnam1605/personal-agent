# Skills and Graphs Lifecycle Architecture Contract

## 1. Concept Definition

### What is a Skill?
A **Skill** is a structured, prompt-and-metadata document defining a high-level procedure, its preconditions, capabilities, recommended steps, and success criteria.

```text
skills/
└── meeting-prep/
    ├── SKILL.md
    └── metadata.yaml
```

- `metadata.yaml`: Contains skill identifier, required tools/domains, trigger patterns, version, and author.
- `SKILL.md`: Detailed markdown instructions for the agent runtime describing the step-by-step reasoning procedure.

`meeting-prep` requires calendar + Gmail + Drive + retrieval together. No
single P12/P13 specialist holds all four capabilities; the intended activator
is the Supervisor (P16) or a later compiled `MeetingPrepGraph` (P19).
`email-follow-up` can run on `CommunicationAgent` for its read steps, then
must stop at `NEEDS_APPROVAL` before `gmail.create_draft`.

Hardening telemetry (ADR 0005) records the **observed** tool path after
`SpecialistRunner.run()`, never the declared `SKILL.md` step list at
activation time.

### What is a Static Workflow Graph?
A **Static Workflow Graph** is a compiled Python LangGraph workflow with deterministic state models, parallel execution nodes, and explicit transition edges.

---

## 2. The Skills-to-Graph Lifecycle

```text
+-------------------------------------------------------------+
| 1. PROCEDURE IDEA                                           |
| User or engineer identifies a multi-step user goal.        |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
| 2. DYNAMIC SKILL SPECIFICATION                              |
| Author SKILL.md and metadata.yaml in skills/ directory.    |
| Executed dynamically by Supervisor or Specialist agent.     |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
| 3. TRACE & EVALUATION COLLECTION                            |
| Instrument runs; record LangSmith traces, tool calls,       |
| latency, token costs, and user satisfaction.                |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
| 4. STABILITY ASSESSMENT                                     |
| Check if execution steps are consistent and predictable.   |
| Has it achieved >= 50 trace samples with > 90% stability?   |
+-------------------------------------------------------------+
                              |
               +--------------+--------------+
               |                             |
              YES                            NO
               |                             |
               v                             v
+-----------------------------+ +-----------------------------+
| 5. COMPILED STATIC GRAPH    | | Keep as Dynamic Skill       |
| Implement in app/workflows/ | | Iterate on prompt / steps   |
| Register in WorkflowRegistry| +-----------------------------+
+-----------------------------+
```

---

## 3. Normative Rules for Skill Evolution
1. **No Early Optimization**: Engineers **MUST NOT** hard-code a complex LangGraph workflow for an untested workflow. Always start with a `Skill`.
2. **Skill Registry Integration**: `SkillRegistry` dynamically loads markdown skills and makes them discoverable to `SupervisorAgent` and `FastTriage`.
3. **Hardening Decision Package**:
   - Before moving a skill into a static graph, a **Workflow Hardening Report** must be submitted containing:
     - Comparison of Dynamic vs Static latency (p50/p95).
     - Token consumption reduction percentage.
     - Error/failure rate comparison across the evaluation test set.
4. **Backward Compatibility**: When a skill is hardened into a static graph, the skill definition remains in the registry as an archival reference or fallback mechanism.
