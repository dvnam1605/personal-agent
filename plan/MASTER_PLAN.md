# PERSONAL MULTI-AGENT ASSISTANT — EXECUTION-GRADE IMPLEMENTATION PLAN V4

**Document status:** Architecture contract + execution specification  
**Target:** Personal Multi-Agent Assistant V1, designed for future scale  
**Primary language:** Python  
**Agent/runtime direction:** Multi-agent capability boundaries + bounded specialist loops + graph workflows  
**Core orchestration:** LangGraph as execution substrate + first-party orchestration policy (see §18A, ADR 0011)  
**Backend:** FastAPI + Pydantic  
**Data:** PostgreSQL + pgvector + Redis  
**Integrations V1:** Gmail, Google Contacts, Google Calendar, Google Drive  
**Knowledge:** PDF/DOCX ingestion, hybrid retrieval, reranking, citations  
**Research:** Internal knowledge + Drive + web research  
**Observability:** LangSmith + internal run/tool audit  
**Mandatory workflow:** Implement → Test → Self-review → User Review → APPROVED → Next Phase

---


# 0A. HOW TO USE THIS FILE WITH CURSOR / ANTIGRAVITY

Recommended initial instruction:

```text
Read IMPLEMENTATION_PLAN_PERSONAL_AI_ASSISTANT_V4.md completely.

Current authorized phase: P0.

Implement ONLY P0 according to:
- the global architecture rules,
- P0 task definitions,
- the Coding-Agent Phase Execution Protocol,
- the P0 Execution Card.

Do not implement future phases.

When P0 is complete:
- run all required checks,
- self-review against the plan,
- produce PHASE P0 REVIEW PACK,
- mark WAITING FOR USER REVIEW — P0,
- STOP.
```

For later phases replace `P0` with the explicitly approved phase.

The coding agent should treat headings containing:

```text
HARD
MUST
MUST NOT
FORBIDDEN
NORMATIVE
```

as non-negotiable constraints unless the user approves an Architecture Change Request.



# 0B. PLAN FILE ORGANIZATION — NORMATIVE

The canonical source is maintained in two forms:

```text
1. Monolithic reference:
   IMPLEMENTATION_PLAN_PERSONAL_AI_ASSISTANT_V4.md

2. Modular execution package:
   personal_ai_assistant_plan_v4/
```

For human review, the monolithic file is convenient.

For Cursor/Antigravity/Codex execution, use the modular package.

A coding agent should normally read only:

```text
MASTER_PLAN.md
+
CURRENT_PHASE.md / phases/PXX_*.md
+
any explicitly referenced prior ADRs
```

It should NOT load all 20 phase files into context by default.

This reduces:

- context dilution;
- accidental future-phase implementation;
- contradictory interpretations;
- token usage;
- slow planning.

The monolithic file remains the complete archival/reference copy.


# 0. WHY THIS V2 EXISTS

This plan supersedes V1, V2 and V3. V4 changes document chunking/retrieval to hierarchical structure-aware parent–child retrieval and is intended to be executable phase-by-phase by Cursor, Antigravity, Codex, Claude Code, or another coding agent with minimal architectural guessing.

The first plan was structurally correct but too eager to route complex logic through:

```text
Router LLM
 -> Supervisor LLM
 -> Specialist LLMs
 -> Evaluator LLM
 -> Synthesis LLM
```

for too many requests.

That architecture scales functionally, but creates unnecessary:

- latency
- token usage
- orchestration complexity
- debugging difficulty
- repeated model reasoning for known procedures

V2 keeps the system MULTI-AGENT from the beginning, but adopts a stricter execution rule:

> Multi-agent is a capability boundary, not an execution requirement.

Having multiple agents does NOT mean every request must invoke multiple agents.

V2 therefore uses three execution paths:

```text
                           REQUEST
                              |
                              v
                         Fast Triage
                              |
          +-------------------+--------------------+
          |                   |                    |
          v                   v                    v
    SINGLE DOMAIN       KNOWN WORKFLOW        OPEN COMPLEX
          |                   |                    |
          v                   v                    v
   Specialist Agent      Static Graph         Supervisor
          |                   |                Dynamic Plan
          |              Specialist Agents         |
          |                   |                Dynamic Graph
          +-------------------+--------------------+
                              |
                              v
                           RESULT
```

This preserves multi-agent scalability while avoiding multi-agent overhead when it is not needed.

---

# 1. V1 PRODUCT SCOPE

## 1.1. Goal

Build a personal AI assistant that can:

1. Understand natural-language user requests.
2. Work with Gmail:
   - search
   - read
   - summarize
   - draft
   - reply
   - send
   - archive
   - label
3. Resolve people through Google Contacts.
4. Work with Google Calendar:
   - list/search events
   - free/busy
   - find slots
   - create/update/delete events
5. Work with Google Drive:
   - search/list
   - read/download
   - upload
   - create folders
   - rename
   - move
   - delete
   - manage supported permissions
6. Ingest a large internal corpus of PDF/DOCX documents.
7. Perform hybrid document retrieval.
8. Rerank evidence.
9. Answer document questions with source references.
10. Search internal knowledge and external web sources.
11. Compare internal documents against current external information.
12. Maintain useful conversation/entity/preference memory.
13. Execute write actions safely through a Policy/Approval layer.
14. Support repeatable workflows such as meeting preparation.
15. Support open-ended cross-domain tasks through a Supervisor.
16. Remain scalable to future specialist agents without redesigning the core runtime.
17. Measure latency, tool calls, LLM calls, iterations and cost for every meaningful run.

---

# 2. V1 AGENTS

V1 has exactly four agent roles.

```text
AgentRegistry
|
+-- SupervisorAgent
|
+-- CommunicationAgent
|    +-- Gmail
|    +-- Contacts
|
+-- CalendarAgent
|    +-- Google Calendar
|
+-- KnowledgeResearchAgent
     +-- Internal RAG
     +-- Drive read/search
     +-- Web research
```

## 2.1. SupervisorAgent

Purpose:

- handle open complex multi-domain requests
- create structured execution plans
- select specialists
- define task dependencies
- trigger bounded replanning only when required

Supervisor is NOT invoked for:

- simple single-domain requests
- requests matched to a known static workflow
- deterministic tool operations that do not need decomposition

## 2.2. CommunicationAgent

Owns communication-domain reasoning.

Tools include:

```text
Gmail
Contacts
```

It can run:

```text
DIRECT MODE
or
BOUNDED REACT MODE
```

depending on complexity.

## 2.3. CalendarAgent

Owns calendar/availability reasoning.

It must prefer deterministic scheduling utilities over LLM arithmetic.

It can run:

```text
DIRECT MODE
or
BOUNDED REACT MODE
```

## 2.4. KnowledgeResearchAgent

Owns the most open-ended specialist domain.

Capabilities:

```text
internal retrieval
Drive search/read
PDF/DOCX evidence
reranking
web research
multi-source comparison
```

This agent is expected to use bounded ReAct more frequently than the other specialists.

---

# 3. NON-AGENT SERVICES

The following MUST NOT become agents unless a later approved architecture change proves that they need autonomous reasoning:

```text
MemoryService
EntityResolver
PolicyEngine
ApprovalManager
Retriever
Reranker
EmbeddingService
DocumentParser
WorkflowRegistry
SkillRegistry
ToolRegistry
CapabilityGate
MemoryGate
LatencyBudgetManager
```

This is intentional.

Do not create agents merely because a subsystem has a name.

---

# 4. V1 NON-GOALS

Explicitly out of scope until V1 is approved:

- Slack
- Microsoft Teams
- Jira
- Linear
- GitHub project agent
- CRM
- Finance agent
- Travel agent
- HR agent
- Voice
- company multi-tenancy
- graph database such as Neo4j
- Kafka
- Kubernetes
- dedicated vector DB unless benchmark proves pgvector insufficient
- unrestricted agent-to-agent conversation
- autonomous destructive actions
- unbounded ReAct
- unbounded Supervisor replanning
- autonomous payment or financial transactions

---

# 5. DESIGN PRINCIPLES

## 5.1. Multi-agent is a capability boundary

The system may have many specialists while invoking only one per request.

Example:

```text
"Lịch mai?"

Request
 -> CalendarAgent
 -> calendar.list_events
 -> response
```

No Supervisor.

No CommunicationAgent.

No KnowledgeResearchAgent.

---

## 5.2. Loop first, graph when shape is known

Use a loop when the next step is uncertain.

Example:

```text
ResearchAgent
 -> search internal
 -> inspect evidence
 -> read more
 -> maybe web search
 -> inspect evidence
 -> finish
```

Use a graph when the workflow shape is already known.

Example:

```text
Meeting Prep

Find meeting
     |
     v
+----+----------------+
|                     |
v                     v
Communication      Knowledge
|                     |
+---------+-----------+
          |
          v
       Synthesis
```

Do NOT graph every chat request.

Do NOT leave stable repeated workflows permanently dependent on Supervisor planning.

---

## 5.3. Skills before hard-coded graphs

A procedure may begin as a Skill.

Example:

```text
skills/
└── meeting-prep/
    ├── SKILL.md
    └── metadata.yaml
```

A Skill describes:

- objective
- inputs
- required capabilities
- recommended procedure
- completion criteria
- expected output
- safety constraints

Initially:

```text
Supervisor / Specialist
 -> reads Skill
 -> executes dynamically
```

After evaluations show that the procedure is stable:

```text
Skill
 -> harden
 -> Static Workflow Graph
```

Rule:

> Do not hard-code a graph simply because a workflow occurred once.

Graph hardening requires evidence from traces/evaluation.

---

## 5.4. Direct first, ReAct only when needed

Every specialist supports two modes.

### DIRECT MODE

Use for:

- exact lookup
- known deterministic operation
- one obvious tool call
- simple tool + formatting

Example:

```text
"Lịch ngày mai?"

CalendarAgent
 -> list_events
 -> response
```

### REACT MODE

Use when:

- tool choice depends on observation
- multiple searches may be needed
- ambiguity must be resolved
- evidence sufficiency must be assessed

Example:

```text
"Tìm email gần đây của Nam về RAG và cho tôi quyết định cuối cùng."

CommunicationAgent
 -> resolve_contact
 -> search_email
 -> inspect candidates
 -> read_thread
 -> maybe search again
 -> synthesize
```

---

# 6. EXECUTION PATHS

## 6.1. Path A — Single-domain specialist

```text
Request
 -> Fast Triage
 -> Specialist Agent
 -> Direct or ReAct
 -> Result
```

Examples:

```text
"Lịch mai?"
"Email gần nhất của Nam?"
"Tìm tài liệu nói về hybrid retrieval."
```

## 6.2. Path B — Known workflow graph

```text
Request
 -> Fast Triage
 -> WorkflowRegistry
 -> Static Graph
 -> Specialist nodes / deterministic nodes
 -> Result
```

Examples:

```text
"Chuẩn bị cuộc họp ngày mai."
"Tạo daily briefing."
```

## 6.3. Path C — Open complex Supervisor

```text
Request
 -> Fast Triage
 -> Supervisor
 -> Structured Plan
 -> Dynamic DAG
 -> Specialist Agents
 -> Synthesis
```

Example:

```text
"Xem các trao đổi gần đây của tôi với Nam,
đối chiếu tài liệu RAG và xem tuần sau
có lịch nào phù hợp để trao đổi tiếp."
```

---

# 7. FAST TRIAGE DESIGN

The router/triage layer must NOT automatically be a heavyweight LLM agent.

Canonical output:

```python
class RouteDecision(BaseModel):
    domains: list[Domain]
    complexity: Complexity
    route_type: RouteType
    workflow_name: str | None
    confidence: float
```

Possible route types:

```text
DIRECT_SPECIALIST
KNOWN_WORKFLOW
SUPERVISOR
CASUAL_RESPONSE
```

Triage priority:

```text
1. deterministic/explicit route
2. known workflow match
3. cheap classifier/small model if ambiguous
4. Supervisor only for actual open complexity
```

---

# 8. CAPABILITY AND TOOL GATING

This is a HARD architecture rule.

Agents must NOT see every tool.

## 8.1. CommunicationAgent exposure

Allowed:

```text
gmail.*
contacts.*
```

Conditionally allowed mutation tools:

```text
gmail.send
gmail.trash
gmail.archive
gmail.labels
```

subject to PolicyEngine.

## 8.2. CalendarAgent exposure

Allowed:

```text
calendar.*
contacts.resolve_person
```

## 8.3. KnowledgeResearchAgent exposure

Read/research mode:

```text
knowledge.*
drive.search
drive.read
drive.download
web.search
```

It must NOT receive:

```text
gmail.send
calendar.delete
drive.delete
permission.change
```

unless an explicitly approved future workflow requires it.

## 8.4. Read-only execution capability

For research-only tasks, the runtime should construct a read-only ToolRegistry view.

Security rule:

> Prefer preventing dangerous capability exposure over relying on a prompt that says "do not use this tool."

## 8.5. Runtime tool restriction enforcement — HARD

Tool gating MUST be enforced at the ToolRegistry level, not by prompt instruction.

An agent that should not see `gmail.send` MUST NOT receive that tool in its schema.
A filtered tool MUST reject execution even if called by name.

Implementation:

```text
ToolRestriction:
  allow: list[str] | None   # whitelist — only these tools visible
  deny:  list[str] | None   # blacklist — these tools removed

ToolRegistry.restrict(restriction) -> ScopedToolView
```

Restricted tools MUST:

1. be removed from the tool schema (model never sees them)
2. reject execution if called by name (defense in depth)
3. log the rejection with the requesting agent identity

Security rule (reaffirmed):

> Prefer preventing dangerous capability exposure over relying on a prompt
> that says "do not use this tool."

## 8.6. Delegation scope context injection

Every delegated specialist MUST receive an injected runtime context
statement informing it of its scope limitations:

```text
DELEGATION_CONTEXT = (
    "You are a delegated specialist: your permission scope was fixed "
    "when you were started and cannot be widened from inside this "
    "execution. Operations that require approval are rejected "
    "automatically. When the task needs access beyond that scope, "
    "return a CapabilityRequest so the orchestration layer can handle it."
)
```

This is injected as a system-level context, not a user instruction.
It does not replace runtime enforcement — it supplements it so the
LLM understands its boundaries.

---

# 9. MEMORY GATE

Do not retrieve full memory on every turn.

Pipeline:

```text
Request
   |
   v
Memory Gate
 /        \
NO        YES
|          |
|      retrieve relevant
|       memory/context
|          |
+----------+
     |
     v
 execution
```

Memory retrieval should be triggered when:

- resolving ambiguous references
- using user preferences
- continuing prior context
- interpreting "that file", "that email", "as before"
- recalling known entities
- supporting a long-running user workflow

Simple requests should not automatically trigger expensive memory retrieval.

---

# 10. MEMORY CONSOLIDATION RULE

Memory extraction/consolidation SHOULD NOT be on the critical response path unless necessary.

Preferred:

```text
User request
 -> response returned
 -> background memory candidate extraction
 -> validation/persistence
```

The assistant must never block a simple response just to perform optional memory consolidation.

---

# 11. LATENCY + LLM CALL BUDGET

Latency is an architecture metric, not only a later optimization.

Every run must record:

```text
route_type
agents_activated
LLM_calls
tool_calls
react_steps
supervisor_iterations
parallel_time
total_latency
token_usage
estimated_cost
```

## 11.1. Initial budgets

These are initial engineering budgets, not guaranteed SLA.

### Simple single-domain

Target:

```text
0–1 primary generative LLM calls
No Supervisor
No evaluator LLM
```

Typical target latency:

```text
< 2–3 seconds where provider/tool latency allows
```

### Delegation chain

Target:

```text
max_delegation_depth = 3
```

A depth-1 specialist executing under Supervisor can delegate at most
to depth 3. Exceeding the limit is a hard rejection, not a soft warning.

### Adaptive single-domain / specialist ReAct

Target:

```text
1–3 meaningful model turns
bounded tool loop
```

Typical:

```text
2–6 seconds
```

### Known workflow graph

Target:

```text
parallel deterministic/tool nodes
minimum number of synthesis/model calls
```

Avoid one LLM call per graph node unless reasoning is truly required.

### Open complex Supervisor

Allowed:

```text
Supervisor planning
specialist work
final synthesis
bounded replan
```

But every extra LLM call must have an explicit reason.

---

# 12. "NO LLM WHEN CODE IS ENOUGH" RULE

Do not use an LLM for:

- Calendar free/busy calculation
- slot arithmetic
- exact contact match
- Drive exact filename search
- Gmail provider query execution
- vector retrieval
- FTS retrieval
- deterministic permission check
- dependency graph validation
- idempotency
- retry logic
- pagination

Use LLMs for:

- ambiguous natural language
- open-ended tool selection
- planning
- research sufficiency
- synthesis
- semantic comparison
- complex reference resolution when deterministic resolution fails

---

# 13. PARALLELISM RULE

Independent work should run in parallel.

Example:

```text
Meeting identified
      |
      +--------------------+
      |                    |
      v                    v
 Email research       Document research
      |                    |
      +---------+----------+
                |
                v
             Synthesis
```

Do not execute independent specialists sequentially unless required by dependencies.

---

# 14. REACT POLICY

ReAct is allowed only inside a bounded specialist execution.

Every ReAct execution MUST have:

```text
max_steps
max_tool_calls
timeout
budget
stop criteria
no-progress detection
```

Suggested starting values:

```text
max_steps = 6
max_tool_calls = 8
```

These are configuration values.

Stop on:

```text
sufficient result
explicit terminal tool/result
no meaningful progress
max steps
max tool calls
budget exceeded
timeout
policy denial
```

## 14.1. Delegation depth limit

Every delegation chain MUST have a maximum depth.

```text
max_delegation_depth = 3  (configurable)
```

A specialist executing under Supervisor at depth 1 cannot spawn another
delegation chain beyond depth 3. This prevents:

```text
Supervisor -> Specialist -> sub-delegate -> sub-delegate (unbounded)
Recursive CapabilityRequest loops
```

The depth is tracked in `ExecutionBudget` and enforced by the delegation
service before dispatching to any specialist.

Exceeding the limit is a hard rejection, not a soft warning.

---

# 15. SUPERVISOR POLICY

Supervisor is conditional.

Supervisor MUST NOT be used merely because multiple agents exist.

Use Supervisor when:

```text
multiple domains
+
not a known workflow
+
task decomposition depends on request semantics
```

Supervisor output MUST be structured.

Example:

```json
{
  "goal": "prepare_followup_with_nam",
  "tasks": [
    {
      "id": "t1",
      "capability": "communication",
      "instruction": "Summarize recent RAG-related communication with Nam",
      "depends_on": []
    },
    {
      "id": "t2",
      "capability": "knowledge_research",
      "instruction": "Find internal RAG documents relevant to recent communication",
      "depends_on": ["t1"]
    },
    {
      "id": "t3",
      "capability": "calendar",
      "instruction": "Find suitable meeting slots for next week",
      "depends_on": []
    }
  ]
}
```

---

# 16. AGENT COMMUNICATION RULE

Agents MUST NOT freely call other agents.

Forbidden:

```text
CalendarAgent
 -> CommunicationAgent
 -> KnowledgeAgent
 -> CalendarAgent
```

Required:

```text
Agent
 -> structured result
 -> Shared State / Runtime
 -> Supervisor or Workflow Executor
 -> next Agent
```

A specialist that needs another capability returns:

```text
NeedMoreContext
or
CapabilityRequest
```

It does NOT perform hidden peer-to-peer delegation.

## 16.1. Structured report-back protocol

When a specialist needs capability it does not own, it MUST return one of:

```text
NeedMoreContext(reason, what_is_needed)
CapabilityRequest(target_capability, instruction)
```

It MUST NOT:

- attempt peer-to-peer calls
- retry denied operations
- escalate silently

The orchestration layer (Supervisor / Workflow Executor) receives these
structured signals and schedules follow-up work.

For long-running specialist work, the orchestration layer MAY send
follow-up content to a running specialist without restarting it.

```text
Supervisor
  -> start specialist (one-shot or continuable)
  -> specialist returns NeedMoreContext
  -> Supervisor schedules another specialist
  -> Supervisor sends follow-up to original specialist
  -> specialist completes with enriched context
```

This preserves the rule: agents communicate only through the shared
runtime, never through hidden peer-to-peer channels.

---

# 17. ACTION POLICY

Technical full access does not imply automatic action execution.

Canonical action classes:

```text
READ
SAFE_WRITE
SENSITIVE_WRITE
DESTRUCTIVE
EXTERNAL_COMMUNICATION
PERMISSION_CHANGE
```

Initial default:

| Action | Default |
|---|---|
| Read Gmail | AUTO |
| Search Gmail | AUTO |
| Read Calendar | AUTO |
| Search Drive | AUTO |
| Internal RAG | AUTO |
| Web search | AUTO |
| Create email draft | AUTO |
| Create reminder/task | AUTO |
| Archive email | configurable |
| Add/remove label | configurable |
| Create Calendar event | APPROVAL |
| Update Calendar event | APPROVAL |
| Delete Calendar event | APPROVAL |
| Send email | APPROVAL |
| Delete/trash email | APPROVAL |
| Drive move | configurable |
| Drive rename | configurable |
| Drive upload | configurable |
| Drive delete | APPROVAL |
| Permission/share changes | APPROVAL |

No agent can bypass PolicyEngine.

## 17.1. Delegation policy pinning — HARD

When a specialist is invoked by Supervisor or Workflow:

```text
child.approval_policy = NEVER
child.sandbox_scope = parent.sandbox_scope (frozen at delegation time)
```

A delegated specialist:

- CANNOT approve its own actions
- CANNOT widen its permission scope
- MUST reject approval-required operations and return a structured
  `needs_approval` flag in its result
- The orchestration layer collects these and surfaces them to the
  PolicyEngine / ApprovalManager

This is captured BEFORE the first await in delegation, so a later parent
policy change does not affect an in-flight child.

---

# 18. TARGET ARCHITECTURE

```text
                              USER
                                |
                                v
                         Context Builder
                                |
                          +-----+-----+
                          | MemoryGate |
                          +-----+-----+
                                |
                                v
                          Fast Triage
                                |
          +---------------------+----------------------+
          |                     |                      |
          v                     v                      v
   DIRECT SPECIALIST      KNOWN WORKFLOW          OPEN COMPLEX
          |                     |                      |
          v                     v                      v
  Specialist Agent        Static Graph            Supervisor
          |                     |                 Structured Plan
     Direct/ReAct          Specialist Nodes             |
          |                     |                  Dynamic DAG
          +---------------------+----------------------+
                                |
                                v
                         Shared Runtime
                                |
                +---------------+----------------+
                |               |                |
                v               v                v
       CommunicationAgent CalendarAgent KnowledgeResearchAgent
                |               |                |
           Direct/ReAct     Direct/ReAct      Direct/ReAct
                |               |                |
          Gmail/Contacts      Calendar       RAG/Drive/Web
                |               |                |
                +---------------+----------------+
                                |
                                v
                         PolicyEngine
                                |
                         +------+------+
                         |             |
                        AUTO        APPROVAL
                                |
                                v
                               END
```

Supporting layers:

```text
SkillRegistry
WorkflowRegistry
AgentRegistry
ToolRegistry
CapabilityGate
MemoryService
EntityResolver
Retriever
Reranker
EmbeddingService
DocumentParser
LatencyBudgetManager
Trace/Audit
```

---

# 18A. ORCHESTRATION SUBSTRATE — NORMATIVE

Governing decision: ADR 0011. Detail: `docs/architecture/orchestration-substrate.md`.

LangGraph is the **execution substrate**. It is NOT the orchestration policy.

```text
ORCHESTRATION POLICY  (first-party code — reviewable, testable, owned)
FastTriage · RouteDecision · Supervisor planning · DAG validation
CapabilityGate · LatencyBudgetManager · ReAct stop logic
                          |
                          | drives
                          v
EXECUTION SUBSTRATE  (LangGraph)
StateGraph · Send · interrupt · checkpointer · streaming · LangSmith
```

The rule:

> LangGraph decides HOW execution is carried out.
> It NEVER decides WHAT should execute.

## 18A.1. Substrate primitives and their owning phase

| Primitive | Phase | Purpose |
|---|---|---|
| `StateGraph` + typed channels | P11, P15 | graph topology and state merge |
| `Send` | P15, P16 | dynamic fan-out for independent tasks |
| conditional edges | P15, P16 | route dispatch, bounded replan edge |
| `interrupt()` | P18 | human-in-the-loop pause |
| `AsyncPostgresSaver` | P18 | durable resume across restart |
| `astream` / `astream_events` | post-V1 | response streaming |
| `LocalFileSpillStore` (Spill) | P04 (P4-08) | large payload spillover to disk/cache |
| `RepeatGuard` + Report tool | P11 (P11-09/10) | loop-prevention & structured specialist report |
| Context Compaction / Pruner | P17 (P17-08..10) | conversation compaction and memory pruning |
| `tool_ask_user` | P18 (P18-02A) | interactive HITL clarification before mutation |

## 18A.2. MUST remain first-party

The following MUST NOT be delegated to any prebuilt abstraction:

```text
FastTriage / RouteDecision
Supervisor structured planning
DAG validation
CapabilityGate
LatencyBudgetManager
bounded ReAct stop / no-progress logic
CapabilityRequest scheduling
```

`ExecutionPlan.topological_order()` and `get_ready_tasks()` are retained.
They select the runnable batch; `Send` only dispatches it.
Scheduling authority stays in first-party code.

## 18A.3. FORBIDDEN

```text
langgraph_supervisor / create_supervisor
create_handoff_tool or any control-transfer handoff between agents
tool-wrapped subagents as the Supervisor mechanism
```

Reasons: the package is unmaintained upstream; message-history handoff cannot
express `depends_on` dependencies (P16-01, P16-03); it serializes agents and so
violates §13 parallelism and P16-04; control transfer between agents violates §16
and ADR 0009; and it places a Supervisor LLM in front of every request, which
violates §6.1 and the P15-07 budget assertion.

## 18A.4. Two persistence layers — do not merge

| Concern | Owner |
|---|---|
| resumability (thread state, pending interrupts, replay) | LangGraph checkpointer on PostgreSQL |
| observability, audit, budget accounting | P3 tables `assistant_runs`, `tool_executions`, `llm_executions`, `audit_events` |

Rules:

- `assistant_runs.state_snapshot` is a sanitized read-only projection. It is NEVER a resume source.
- The checkpointer is subject to the same P3 redaction policy.
- Redis keeps only cache, rate limit, and short-term history. It is NOT a checkpoint store.
- Audit tables stay append-only regardless of checkpoint rollback or replay.

## 18A.5. Dependency boundary

```text
app/domain/      zero framework imports — pure Pydantic contracts
app/services/    zero framework imports
app/agents/      LangChain model/tool interfaces only
app/harness/     the ONLY layer permitted to import langgraph
```

`AssistantState` uses `extra="forbid"` and `validate_assignment=True` and MUST NOT
be handed to `StateGraph` unchanged. P11 defines an explicit channel schema with
reducers and converts at the harness boundary.

## 18A.6. Driver note

`langgraph-checkpoint-postgres` uses psycopg3 while the application data layer uses
asyncpg via SQLAlchemy. Both drivers coexist deliberately: asyncpg for ORM/domain
access, psycopg3 for the checkpointer only. `psycopg[binary]` is required so that
libpq ships with the wheel. The checkpointer DSN must be derived from
`DATABASE__URL` by stripping the `+asyncpg` marker; that derivation is a P11 task.

## 18A.7. Embedding dimensions and migration contract

- **Dimension Canonical Choice**: All vector columns (`document_chunks.embedding`, `memories.embedding`) use **Vector(1024)** powered by local model `AITeamVN/Vietnamese_Embedding` (ADR-0012).
- **Superseded Baseline**: The initial exploratory mention of `Vector(1536)` (OpenAI `text-embedding-3-large`) was superseded in P09A/P09D/P10 to guarantee data sovereignty, zero API egress costs, and offline reproducibility.
- **Migration & Backfill Path**: Alembic migrations `0005` and `0008` standardized the PostgreSQL column type via `ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(1024) USING NULL::vector;`. Existing records with null embeddings must be re-embedded via `python scripts/ingest_corpus.py` (which parses and computes 1024-dim dense vectors along with companion OCR sidecars).

---

# 19. REPOSITORY TARGET

```text
app/
├── api/
│   ├── routes/
│   └── dependencies/
│
├── agents/
│   ├── supervisor/
│   ├── communication/
│   ├── calendar/
│   └── knowledge_research/
│
├── harness/
│   ├── triage/
│   ├── routing/
│   ├── react/
│   ├── planning/
│   ├── execution/
│   ├── graphs/
│   ├── workflows/
│   ├── skills/
│   ├── state/
│   └── budgets/
│
├── tools/
│   ├── gmail/
│   ├── contacts/
│   ├── calendar/
│   ├── drive/
│   ├── knowledge/
│   └── web/
│
├── services/
│   ├── memory/
│   ├── entities/
│   ├── policy/
│   ├── approvals/
│   ├── retrieval/
│   ├── ingestion/
│   └── capabilities/
│
├── infrastructure/
│   ├── db/
│   ├── redis/
│   ├── google/
│   ├── llm/
│   └── observability/
│
├── domain/
│   ├── models/
│   ├── enums/
│   └── errors/
│
├── core/
│   ├── config.py
│   ├── logging.py
│   └── security.py
│
└── main.py

skills/
├── meeting-prep/
└── ...

tests/
├── unit/
├── integration/
├── evaluation/
├── e2e/
└── fixtures/

docs/
├── architecture/
├── adr/
└── reviews/
```

---

# 20. MANDATORY PHASE-GATE PROCESS

This is a HARD project rule.

For every phase:

```text
IMPLEMENT PHASE
      |
      v
RUN TESTS
      |
      v
SELF REVIEW
      |
      v
PREPARE REVIEW PACK
      |
      v
STOP
      |
      v
USER REVIEW
  |              |
  |              |
APPROVED       CHANGES REQUESTED
  |              |
  v              v
NEXT PHASE     FIX CURRENT PHASE
```

Proceed ONLY after an explicit command equivalent to:

```text
APPROVED PN
```

If the user requests changes:

```text
CHANGES REQUESTED PN
- ...
```

then only fix Phase N.

After the fixes:

```text
rerun tests
 -> new Phase N Review Pack
 -> STOP
```

---

# 21. FORBIDDEN PHASE-GATE BEHAVIOR

Before approval of Phase N:

- Do not implement Phase N+1.
- Do not add future business logic "because it is convenient".
- Do not refactor approved architecture silently.
- Do not introduce another agent without approved architecture change.
- Do not replace the approved orchestration pattern silently.
- Do not turn a Skill into a Graph without the required hardening review.
- Do not add Supervisor to a path simply to make it "more agentic".
- Do not bypass latency budgets without documenting the reason.

Allowed:

- interface
- protocol
- types
- empty adapter
- dependency injection hook
- config placeholder

only if genuinely necessary for the active phase.

---

# 22. REVIEW PACK FORMAT

Every phase MUST end with:

```text
PHASE PN REVIEW PACK

1. Phase objective
2. Tasks completed
3. Files created
4. Files modified
5. Architecture decisions
6. Public contracts changed
7. Database migrations
8. Tests added
9. Test results
10. Manual verification
11. LLM-call/latency observations
12. Security/privacy notes
13. Known limitations
14. Deferred items
15. Deviations from plan
16. Diff summary
17. Suggested reviewer focus
18. Gate status: WAITING FOR USER REVIEW
```

Then STOP.

---


# 22A. CODING-AGENT PHASE EXECUTION PROTOCOL — NORMATIVE

This section is mandatory for Cursor/Antigravity/Codex/Claude Code or any coding agent implementing the project.

The plan is not only a feature checklist. It is an execution contract.

For every active phase `PN`, the coding agent MUST follow this sequence:

```text
READ PLAN + APPROVED ADRs
        |
        v
INSPECT CURRENT REPOSITORY
        |
        v
CHECK ENTRY CRITERIA
        |
        v
CREATE PHASE TASK CHECKLIST
        |
        v
IMPLEMENT TASKS IN ORDER
        |
        v
TEST EACH TASK GROUP
        |
        v
RUN PHASE-WIDE QUALITY GATES
        |
        v
SELF REVIEW AGAINST PLAN
        |
        v
CREATE PHASE REVIEW PACK
        |
        v
STOP — WAIT FOR USER
```

## Before touching code

The coding agent MUST:

1. Read this full plan.
2. Determine the currently authorized phase from user instruction and repository review history.
3. Read ADRs created by prior phases that affect the current phase.
4. Inspect existing files before proposing new abstractions.
5. Verify current tests pass before changing code unless the user explicitly asks to repair an already-broken baseline.
6. Confirm the phase entry criteria below are satisfied.
7. Convert the phase tasks into an internal checklist and execute only those tasks.

## While implementing

For every task ID:

```text
Task ID
 -> inspect dependencies
 -> implement minimum compliant solution
 -> add/adjust tests
 -> run focused tests
 -> record deviations
```

Do not batch unrelated architectural changes.

If the plan gives an interface or contract, preserve it unless an Architecture Change Request is approved.

## When a design detail is unspecified

Use this priority:

```text
approved ADR
>
this plan
>
existing project convention
>
simplest implementation preserving extensibility
```

Do NOT invent a new framework or infrastructure layer merely because it is available.

## When blocked

If the implementation is blocked by unavailable credentials/external services:

- implement the adapter boundary;
- add deterministic mocks/fakes;
- add integration-test markers;
- document the manual verification step;
- do NOT silently skip the feature;
- do NOT proceed to the next phase.

## Required phase-wide commands

Each repository should define equivalent commands/scripts for:

```text
lint
typecheck
unit-test
integration-test
phase-specific-test
```

A coding agent must report the exact commands used and their result in the Review Pack.

## Review behavior

At phase completion the coding agent MUST NOT say "continuing to the next phase".

It must end with:

```text
GATE STATUS: WAITING FOR USER REVIEW — PN
```

and stop.


# 23. UPDATED PHASE MAP

```text
P0  Architecture Contract + Execution Principles
 |
P1  Project Foundation
 |
P2  Core Domain Models + Runtime Contracts
 |
P3  State Persistence + Redis + Observability + Budgets
 |
P4  Agent/Tool/Capability Registry + Gating
 |
P5  Google Authentication Foundation
 |
P6  Communication Tools: Gmail + Contacts
 |
P7  Calendar Tools
 |
P8  Drive File Tools
 |
P9  Document Ingestion Pipeline (split: P9A-P9E, separate gates)
 |
P10 Retrieval / RAG Engine (split: P10A-P10D, separate gates)
 |
P11 Specialist Agent Runtime + Bounded ReAct
 |
P12 CommunicationAgent + CalendarAgent
 |
P13 KnowledgeResearchAgent
 |
P14 Skill System + First Dynamic Skills
 |
P15 Fast Triage + Static Workflow Registry
 |
P16 Supervisor + Dynamic Multi-Agent DAG
 |
P17 Context + Memory + Entity Resolution + Memory Gate
 |
P18 Policy + Approval + Full Write Access
 |
P19 Workflow Hardening + Meeting Prep Graph
 |
P20 End-to-End Evaluation + Security Hardening + V1 Release
```

No phase may be skipped without explicit user approval.

---

# MODULAR PHASE INDEX

Read `MASTER_PLAN.md` first, then only the currently approved phase file.

- P0: `phases/P00_architecture_contract_execution_principles.md` — ARCHITECTURE CONTRACT + EXECUTION PRINCIPLES
- P1: `phases/P01_project_foundation.md` — PROJECT FOUNDATION
- P2: `phases/P02_core_domain_models_runtime_contracts.md` — CORE DOMAIN MODELS + RUNTIME CONTRACTS
- P3: `phases/P03_state_persistence_redis_observability_budgets.md` — STATE PERSISTENCE + REDIS + OBSERVABILITY + BUDGETS
- P4: `phases/P04_agent_tool_capability_registry_gating.md` — AGENT / TOOL / CAPABILITY REGISTRY + GATING
- P5: `phases/P05_google_authentication_foundation.md` — GOOGLE AUTHENTICATION FOUNDATION
- P6: `phases/P06_communication_tools_gmail_contacts.md` — COMMUNICATION TOOLS: GMAIL + CONTACTS
- P7: `phases/P07_calendar_tools.md` — CALENDAR TOOLS
- P8: `phases/P08_drive_file_tools.md` — DRIVE FILE TOOLS
- P9: `phases/P09_document_ingestion_pipeline_execution_specification.md` — DOCUMENT INGESTION PIPELINE — SPLIT OVERVIEW
  - P9A: `phases/P09a_ingestion_foundation_contracts_specification.md` — INGESTION FOUNDATION + CONTRACTS (gate: `APPROVED P9A`)
  - P9B: `phases/P09b_parsing_layer_specification.md` — PARSING LAYER (gate: `APPROVED P9B`)
  - P9C: `phases/P09c_chunking_engine_specification.md` — CHUNKING ENGINE (gate: `APPROVED P9C`)
  - P9D: `phases/P09d_embedding_orchestration_specification.md` — EMBEDDING + ORCHESTRATION (gate: `APPROVED P9D`)
  - P9E: `phases/P09e_offline_ocr_batch_script_specification.md` — OFFLINE OCR BATCH SCRIPT (gate: `APPROVED P9E`; independent of B/C/D order)
- P10: `phases/P10_retrieval_rag_engine_execution_specification.md` — RETRIEVAL / RAG ENGINE — SPLIT OVERVIEW
  - P10A: `phases/P10_retrieval_rag_engine_execution_specification.md` (P10-01..05) — FOUNDATION & PG VECTOR/FTS (gate: `APPROVED P10A`)
  - P10B: `phases/P10_retrieval_rag_engine_execution_specification.md` (P10-06..13) — PROCESSING PIPELINE (gate: `APPROVED P10B`)
  - P10C: `phases/P10_retrieval_rag_engine_execution_specification.md` (P10-14..20) — QUALITY POLICIES & RETRY (gate: `APPROVED P10C`)
  - P10D: `phases/P10_retrieval_rag_engine_execution_specification.md` (P10-21..24) — VIRANKER BENCHMARK & FACTORY (gate: `APPROVED P10D` / `APPROVED P10`)
- P11: `phases/P11_specialist_agent_runtime_bounded_react.md` — SPECIALIST AGENT RUNTIME + BOUNDED REACT
- P12: `phases/P12_communicationagent_calendaragent.md` — COMMUNICATIONAGENT + CALENDARAGENT
- P13: `phases/P13_knowledgeresearchagent.md` — KNOWLEDGERESEARCHAGENT
- P14: `phases/P14_skill_system_first_dynamic_skills.md` — SKILL SYSTEM + FIRST DYNAMIC SKILLS
- P15: `phases/P15_fast_triage_static_workflow_registry.md` — FAST TRIAGE + STATIC WORKFLOW REGISTRY
- P16: `phases/P16_supervisor_dynamic_multi_agent_dag.md` — SUPERVISOR + DYNAMIC MULTI-AGENT DAG
- P17: `phases/P17_context_memory_entity_resolution_memory_gate.md` — CONTEXT + MEMORY + ENTITY RESOLUTION + MEMORY GATE
- P18: `phases/P18_policy_approval_full_write_access.md` — POLICY + APPROVAL + FULL WRITE ACCESS
- P19: `phases/P19_workflow_hardening_meeting_prep_graph.md` — WORKFLOW HARDENING + MEETING PREP GRAPH
- P20: `phases/P20_end_to_end_evaluation_security_hardening_v1_release.md` — END-TO-END EVALUATION + SECURITY HARDENING + V1 RELEASE

# 24. EVALUATION FRAMEWORK

## 24.1 Routing evaluation

Track:

```text
correct specialist
known workflow match
unnecessary Supervisor rate
missed complex task rate
```

## 24.2 Specialist evaluation

Track:

```text
tool choice
steps
failure rate
ReAct loop length
```

## 24.3 Supervisor evaluation

Track:

```text
correct capabilities
valid dependencies
task explosion
replan count
```

## 24.4 RAG evaluation

Track:

```text
Recall@K
MRR/nDCG
citation correctness
human relevance
```

## 24.5 Latency/cost evaluation

For each route:

```text
p50 latency
p95 latency
LLM calls
tool calls
tokens
cost
```

## 24.6 Graph-hardening evaluation

For every hardened workflow compare:

```text
dynamic skill
vs
static graph
```

---

# 25. SECURITY HARDENING

Review:

- OAuth tokens
- secret storage
- trace privacy
- document/email data retention
- indirect prompt injection
- tool-argument injection
- unauthorized mutation
- identity ambiguity
- stale approvals
- replay
- idempotency
- capability-gate bypass
- cross-agent state contamination

External content is untrusted data.

Email, PDF, DOCX and web pages MUST NOT be allowed to redefine:

```text
system instructions
tool permissions
PolicyEngine rules
agent capabilities
```

---

# 26. FAILURE / RECOVERY TESTS

Test:

```text
Google API unavailable
LLM unavailable
DB transient failure
Redis unavailable
worker crash
document parse failure
graph node failure
Supervisor partial failure
approval after restart
expired approval
budget exhaustion
```

---

# 27. V1 RELEASE CRITERIA

V1 is accepted only when:

```text
[ ] simple requests bypass Supervisor
[ ] specialist direct mode works
[ ] specialist ReAct is bounded
[ ] multi-agent Supervisor path works
[ ] known workflow infrastructure works
[ ] Skill -> Graph hardening process is demonstrated
[ ] MemoryGate works
[ ] CapabilityGate works
[ ] read-only research cannot mutate
[ ] approval pause/resume works
[ ] Gmail/Calendar/Drive write paths are safe
[ ] RAG benchmark reviewed
[ ] citations are preserved
[ ] prompt injection tests pass
[ ] every run exposes latency + LLM/tool counts
[ ] no known critical unsafe mutation path
[ ] delegation depth enforced at DelegationService level
[ ] delegated specialists receive DELEGATION_CONTEXT
[ ] delegated specialists cannot self-approve
[ ] user approves final V1 review
```

---

# 28. REQUIRED CODE REVIEW RULES

Every phase self-review must check:

## Architecture

- correct layer?
- future-phase logic added accidentally?
- new agent introduced?
- Supervisor used unnecessarily?
- Skill hardened too early?
- graph used where loop is better?
- loop used where deterministic workflow is already known?

## Tool/capability

- agent sees only necessary tools?
- tool restriction enforced at registry level, not prompt?
- research truly read-only?
- mutation classification present?
- policy bypass possible?
- delegated specialist approval pinned to NEVER?

## LLM efficiency

- any unnecessary LLM call?
- can deterministic code replace it?
- independent work parallelized?
- simple request within budget?

## ReAct

- bounded?
- stop reason recorded?
- no-progress protected?
- tool count controlled?

## Memory

- memory retrieval needed?
- unnecessary context loaded?
- optional consolidation blocking response?

## Reliability

- timeout
- retry
- pagination
- idempotency
- partial failure

## Security

- OAuth secrets
- log privacy
- prompt injection
- external content trust boundary
- identity ambiguity

## Tests

- happy
- edge
- failure
- ambiguity
- policy
- budget
- latency path

---

# 29. ARCHITECTURE CHANGE CONTROL

After phase approval, core architecture changes require:

```text
ARCHITECTURE CHANGE REQUEST

Affected phase:
Reason:
Current design:
Proposed design:
Evidence:
Latency impact:
LLM-call impact:
Security impact:
Migration impact:
Test impact:
Risks:
Recommendation:
```

The user must approve architecture-breaking changes.

---

# 30. CODING-AGENT EXECUTION RULE

When using Codex / Claude Code / another coding agent, prepend:

```text
You are implementing a phase-gated multi-agent assistant.

You may ONLY implement the currently authorized phase.

Before coding:
1. Read IMPLEMENTATION_PLAN_PERSONAL_AI_ASSISTANT_V4.md.
2. Identify the active approved gate.
3. Read all ADRs relevant to the phase.
4. Inspect current code.
5. List only active-phase tasks.

Architecture rules:
6. Multi-agent is a capability boundary, not an execution requirement.
7. Simple single-domain requests must not invoke Supervisor.
8. Use Direct mode when deterministic execution is enough.
9. Use bounded ReAct only when adaptive tool selection is needed.
10. Use Skills for evolving procedures.
11. Harden Skills into Graphs only after evaluation supports it.
12. Agents must not directly call other agents.
13. Apply CapabilityGate before exposing tools.
14. Research/read-only runs must not receive mutation tools.
15. Use MemoryGate; do not retrieve full memory every turn.
16. No LLM call when deterministic code is sufficient.
17. Enforce execution budgets.
18. Parallelize independent work.
19. Treat LangGraph as substrate only; orchestration policy stays first-party (§18A).
20. Never use langgraph_supervisor, handoff tools, or tool-wrapped subagents as the Supervisor mechanism.
21. Import langgraph only inside app/harness/.

During implementation:
22. Enforce tool gating at the registry level, not by prompt (§8.5).
23. Inject DELEGATION_CONTEXT into every delegated specialist (§8.6).
24. Enforce max_delegation_depth before dispatching (§14.1).
25. Pin approval_policy=NEVER on delegated children (§17.1).
26. Do not add future-phase business logic.
27. Add/update tests with implementation.
28. Trace important execution decisions.
29. Record deviations.

At phase completion:
30. Run tests.
31. Run lint.
32. Run typecheck.
33. Perform self-review.
34. Produce PHASE PN REVIEW PACK.
35. Mark WAITING FOR USER REVIEW.
36. STOP.

Never begin PN+1 until the user explicitly sends:
APPROVED PN
```

---

# 31. USER REVIEW COMMANDS

## Approve

```text
APPROVED P12
Proceed to P13 only.
```

## Request changes

```text
CHANGES REQUESTED P12

1. ...
2. ...

Do not start P13.
Fix P12, rerun all required tests and submit a new P12 Review Pack.
```

## Full phase review

```text
REVIEW P12 ONLY

Review implementation against IMPLEMENTATION_PLAN_PERSONAL_AI_ASSISTANT_V4.md.

Do not modify code.
Do not start P13.

Return:
- CRITICAL ISSUES
- MAJOR ISSUES
- MINOR ISSUES
- ARCHITECTURE DEVIATIONS
- MISSING TESTS
- LATENCY/LLM BUDGET VIOLATIONS
- SECURITY FINDINGS
- VERDICT
```

## Mini review

```text
MINI REVIEW P12A-01 TO P12A-04

Review only these tasks.
Do not modify code.
Do not review future tasks.
Order findings by severity.
```

---

# 32. GIT STRATEGY

Recommended:

```text
main
 |
 +-- phase/p0-architecture
 +-- phase/p1-foundation
 +-- ...
```

At minimum:

- phase-level commit boundary
- no future-phase work in current phase final commit

Example:

```text
phase(P13): complete knowledge research agent
```

---

# 33. DEFINITION OF PHASE COMPLETE

A phase is complete only when:

```text
[ ] all required tasks implemented
[ ] required tests added
[ ] tests pass
[ ] lint passes
[ ] type check passes
[ ] relevant budget checks pass
[ ] manual verification completed
[ ] docs updated
[ ] security implications reviewed
[ ] no critical known defect
[ ] deviations documented
[ ] Review Pack generated
[ ] user explicitly approves
```

Until user approval, next phase is blocked.

---

# 34. FIRST DEVELOPMENT ORDER

Execution begins:

```text
Implement P0 only
 -> Review Pack
 -> STOP
 -> User review
 -> APPROVED P0
 -> Implement P1 only
```

Do NOT implement P0-P3 together.

The phase gate is part of system engineering quality, not optional process overhead.

---

# 35. LONG-TERM SCALE PATH

The architecture should allow future registration of:

```text
TaskAgent
ProjectAgent
CRM Agent
FinanceAgent
TravelAgent
HR Agent
Dev Agent
```

without changing the core execution model.

Future expansion remains:

```text
Fast Triage
   |
   +--> direct specialist
   |
   +--> known workflow graph
   |
   +--> Supervisor dynamic DAG
```

The runtime should scale by:

```text
new capability
 -> new specialist
 -> register
 -> define gating
 -> evaluate routing
 -> optionally create Skills
 -> harden stable Skills into Graphs
```

not by making every agent talk to every other agent.

---

# 36. FINAL ARCHITECTURE PRINCIPLE

The system should obey this hierarchy:

```text
DETERMINISTIC CODE
       before
DIRECT SPECIALIST
       before
BOUNDED REACT
       before
KNOWN STATIC GRAPH
       when procedure is stable
       and
SUPERVISOR DYNAMIC DAG
       only when open complexity requires it
```

while preserving multi-agent capability boundaries from the beginning.

This is the core architecture contract for Personal Multi-Agent Assistant V1.



# 37. EXECUTION CARDS FOR ALL PHASES — CURSOR / ANTIGRAVITY READY

The phase descriptions above are authoritative. The following cards make the remaining phases directly executable with less guessing.

Each card specifies:

```text
ENTRY -> PROCESS -> EXPECTED OUTPUT -> TEST/PROOF -> EXIT
```

---

## P0 Execution Card

### Entry
No prior phase required.

### Process
1. Create architecture docs only.
2. Turn each major design principle into an ADR.
3. Resolve contradictions inside the plan.
4. Do not scaffold business code.

### Expected output
Architecture documents listed in P0.

### Proof
A reviewer can answer, without reading code:
- when Supervisor runs;
- when ReAct runs;
- when Graph runs;
- which tools each agent may see;
- what requires approval.

### Exit
`WAITING FOR USER REVIEW — P0`.

---

## P1 Execution Card

### Entry
`APPROVED P0`.

### Process
1. Scaffold repository.
2. Configure package management.
3. Add typed config.
4. Add FastAPI health/readiness.
5. Add logging/redaction.
6. Add lint/type/test commands.
7. CI-ready local commands should exist.

### Expected output
Bootable project with no real domain integrations.

### Proof
Fresh checkout + documented setup can run health endpoint and tests.

---

## P2 Execution Card

### Entry
`APPROVED P1`.

### Process
Implement domain contracts before adapters:
1. state;
2. route;
3. agent;
4. tool;
5. evidence;
6. plan;
7. skill;
8. workflow;
9. budget.

### Expected output
Typed models and interfaces with no provider coupling.

### Proof
Serialization/validation tests and contract examples.

---

## P3 Execution Card

### Entry
`APPROVED P2`.

### Process
1. DB + migrations.
2. Redis.
3. run persistence.
4. LLM/tool audit.
5. LangSmith hooks.
6. budget accounting.

### Expected output
A fake run can be persisted/traced end-to-end.

### Proof
Run ID links application log, DB record, tool trace and LLM trace.

---

## P4 Execution Card

### Entry
`APPROVED P3`.

### Process
1. ToolRegistry.
2. AgentRegistry.
3. capability metadata.
4. CapabilityGate.
5. read-only registry view.
6. mutation classification metadata.

### Expected output
Runtime can expose different tool sets to different mock agents.

### Proof
A Research mock cannot retrieve mutation tools even if it asks by name.

---

## P5 Execution Card

### Entry
`APPROVED P4`.

### Process
1. Google OAuth.
2. token encryption.
3. refresh.
4. scopes.
5. client factory.
6. connection health.
7. disconnect.

### Expected output
Authenticated Google integration foundation only.

### Proof
Mock + manual OAuth verification; zero secret leakage.

---

## P6 Execution Card

### Entry
`APPROVED P5`.

### Process
Build deterministic Gmail/Contacts services first, then tool wrappers.

Layering:

```text
Google Adapter
 -> Domain Service
 -> Tool Wrapper
```

Do not put Gmail API response objects directly into agents.

### Proof
All search/read/write operations work through deterministic tests/mocks; mutation class is attached.

---

## P7 Execution Card

### Entry
`APPROVED P6`.

### Process
Build deterministic Calendar adapter/service/tools.

Important:
- timezone normalized at boundary;
- slot finder is code, not LLM;
- conflicts tested;
- mutation metadata emitted.

### Proof
Given fixed busy intervals, slot finder returns deterministic expected slots.

---

## P8 Execution Card

### Entry
`APPROVED P7`.

### Process
Build Drive adapter/service/tools separately from RAG.

Separate:

```text
File management
!=
Semantic document retrieval
```

### Proof
Drive search/move/rename/download can work without pgvector/RAG.

---

## P9 Execution Card (P9A-P9E Document Ingestion Pipeline)

### Entry
`APPROVED P8`.

### Process
1. P9A: Define ingestion contracts, type detection, and logical document versioning models.
2. P9B: Implement parsing layer (MarkdownDocumentParser, DoclingParser, administrative metadata extraction).
3. P9C: Implement hierarchical chunking engine (SectionParentChunker, SentenceChildChunker, TokenBudgets).
4. P9D: Implement local Vietnamese embedding (`AITeamVN/Vietnamese_Embedding`, 1024 dims), pgvector HNSW indexing, and orchestrator state machine.
5. P9E: Implement offline OCR batch script (`scripts/ocr_batch.py`) with sidecar provenance and sha256 checksum tracking.

### Expected output
Complete ingestion pipeline capable of parsing PDF/DOCX/MD, generating hierarchical parent-child chunks, computing 1024-dim dense embeddings, and storing in PostgreSQL.

### Proof
- Document ingestion unit and integration tests pass;
- Scanned documents trigger NEEDS_OCR with audit log;
- OCR sidecars maintain provenance link with orchestrator fingerprinting.

---

## P10 Execution Card (P10A-P10D Retrieval / RAG Engine)

### Entry
`APPROVED P9`.

### Process
1. P10A: Foundation & PostgreSQL Vector/FTS retrieval (dense cosine + sparse tsvector websearch).
2. P10B: Processing pipeline: parallel RRF fusion (k=60), diversity filtering, cross-encoder rerank, expansion (PARENT/NEIGHBORS), and greedy budget packing.
3. P10C: Quality control policies: sufficiency checker, bounded query rewriting/retry, document comparison diversity, and answer synthesis.
4. P10D: ViRanker cross-encoder benchmark, ablations against baseline, and production factory wiring.

### Expected output
Production-grade hybrid RAG engine with Vietnamese document understanding, citation anchor tracking, multi-source external synthesis support, and latency budget adherence.

### Proof
- Comprehensive retrieval benchmark demonstrates hybrid RRF + ViRanker superior to individual baselines;
- Synthesis produces structured answers with verified [evidence_id] citations;
- Multi-source synthesis gracefully handles internal and external knowledge.

---

## P11 Execution Card

### Entry
`APPROVED P10`.

### Process
1. define the LangGraph channel schema + reducers and the `AssistantState` conversion at the harness boundary;
2. derive the psycopg checkpointer DSN from `DATABASE__URL`;
3. implement common specialist runtime;
4. direct mode;
5. ReAct loop;
6. capability view injection;
7. budget enforcement;
8. stop/no-progress logic;
9. trace each iteration.

### Expected output
A domain-neutral fake specialist can execute direct and ReAct modes.

### Proof
Test demonstrates:
- direct path uses no unnecessary agent loop;
- forbidden tool cannot be selected;
- loop stops at configured bound.

---

## P12 Execution Card

### Entry
`APPROVED P11`.

### Process
CommunicationAgent:
1. direct lookup;
2. contact resolution;
3. adaptive email search;
4. summarize;
5. create ProposedAction for mutation.

CalendarAgent:
1. direct listing;
2. deterministic free/busy;
3. adaptive person/constraint resolution;
4. ProposedAction for mutation.

### Proof
Trace simple requests and show they stay on fast paths.

---

## P13 Execution Card

### Entry
`APPROVED P12`.

### Process
1. consume P10 RetrievalEngine;
2. add internal/web source strategy;
3. add bounded evidence-seeking ReAct;
4. preserve evidence provenance;
5. use read-only capability view;
6. return partial/no-answer instead of hallucinating.

### Proof
Research trace shows the agent never reimplements vector/FTS/reranking logic itself.

---

## P14 Execution Card

### Entry
`APPROVED P13`.

### Process
1. SkillDefinition loader.
2. registry/versioning.
3. `meeting-prep` skill.
4. skill execution guidance.
5. telemetry.
6. graph-candidate statistics.

### Proof
Modify skill procedure without changing runtime code.

---

## P15 Execution Card

### Entry
`APPROVED P14`.

### Process
1. deterministic route rules;
2. known-workflow match;
3. small-model fallback only when needed;
4. workflow registry;
5. graph execution primitive built on `StateGraph`;
6. parallel node support via `Send`.

### Proof
Simple Calendar/Email eval never hits Supervisor.

---

## P16 Execution Card

### Entry
`APPROVED P15`.

### Process
1. Supervisor sees capability catalog only.
2. outputs structured plan.
3. validate DAG.
4. execute independent tasks concurrently by dispatching `get_ready_tasks()` batches through `Send`.
5. deterministic result-status evaluation first.
6. bounded replan.
7. final synthesis.

### Proof
Show one trace where two specialists run concurrently and one simple request bypasses Supervisor entirely.

---

## P17 Execution Card

### Entry
`APPROVED P16`.

### Process
1. entity store/resolver;
2. conversation references;
3. preference memory;
4. episodic memory;
5. MemoryGate;
6. compact ContextBuilder;
7. background consolidation.

### Proof
Compare trace:
- trivial request -> no memory retrieval;
- contextual request -> only relevant memory retrieval.

---

## P18 Execution Card

### Entry
`APPROVED P17`.

### Process
1. PolicyEngine.
2. approval persistence.
3. graph interrupt via `interrupt()`.
4. approve/deny APIs.
5. resume via `AsyncPostgresSaver`.
6. stale-action revalidation.
7. idempotency.
8. audit.

### Proof
A send-email action pauses, survives restart if supported by chosen persistence, resumes once, and cannot double-send.

---

## P19 Execution Card

### Entry
`APPROVED P18`.

### Process
1. collect/inspect MeetingPrep Skill traces;
2. quantify shape stability;
3. only then decide whether to graph;
4. implement graph if justified;
5. compare latency/cost/quality.

### Proof
Review Pack contains dynamic-skill vs static-graph benchmark.

If evidence does not justify hardening, the correct implementation result is:
`KEEP AS SKILL`, not a forced graph.

---

## P20 Execution Card

### Entry
`APPROVED P19`.

### Process
1. build evaluation dataset;
2. run route/agent/RAG/safety evaluations;
3. prompt-injection tests;
4. failure/recovery tests;
5. latency/cost report;
6. privacy review;
7. release docs.

### Expected output
A versioned V1 release candidate and evaluation report.

### Exit
`WAITING FOR USER REVIEW — V1 RELEASE`.
