> Active phase specification for P15. Read `../MASTER_PLAN.md` first.
> Do not implement any downstream phase (P16+) until the user sends `APPROVED P15`.

# P15 — FAST TRIAGE + STATIC WORKFLOW REGISTRY SPECIFICATION

## 1. Objective & Architectural Scope

Phase 15 introduces the core routing efficiency and graph execution infrastructure of the assistant:
1. **FastTriage Engine**: Deterministic-first classifier that routes user queries directly to single-domain specialists or known workflows, bypassing the expensive Supervisor LLM planning loop for >= 70% of typical user requests.
2. **RouteDecision Contract**: Canonical, typed routing output with auditability and confidence scores.
3. **Static Workflow Registry**: A registry of pre-compiled, predictable multi-step workflows executed as LangGraph `StateGraph` subgraphs.
4. **LangGraph Substrate Harness**: Establishes first-party patterns for `StateGraph`, conditional edges, and `Send` API parallel execution without delegating planning authority to black-box external frameworks.

### Governing Architectural Rules
- **No Supervisor for Simple Requests (Hard Invariant §6.1)**: Single-domain queries ("Lịch ngày mai?", "Email của Nam?", "Tìm quyết định 123") MUST NOT invoke the Supervisor LLM. Incurring Supervisor planning latency/tokens for a simple query is an architectural defect.
- **First-Party Routing Authority (§18A.2)**: LangGraph determines *how* execution is dispatched across graph nodes; first-party code (`FastTriage`, `RouteDecision`) determines *what* executes.
- **Predictability & Parallelism (§13)**: Workflows with independent branches (e.g., querying Calendar and Drive simultaneously) execute in parallel via the `Send` API.

---

## 2. Entry Criteria

Before P15 implementation begins, all of the following MUST be satisfied:
- [x] **APPROVED P14**: Dynamic skill system and first skills (`MeetingPrepSkill`, `EmailFollowUpSkill`) verified and operational.
- [x] **Specialist Agents Operational**: `CommunicationAgent` (P12), `CalendarAgent` (P12), and `KnowledgeResearchAgent` (P13) operational.
- [x] **Channel Schema Defined (P11)**: Typed channels and state reducers established in `app/harness/channels.py`.

---

## 3. Detailed Component Specifications

### 3.1. RouteDecision Contract (`app/domain/models/route.py`)

```python
class RouteType(str, Enum):
    DIRECT_SPECIALIST = "direct_specialist"  # Route directly to single agent (Communication, Calendar, Research)
    STATIC_WORKFLOW = "static_workflow"      # Route to a pre-registered compiled StateGraph
    SUPERVISOR_DAG = "supervisor_dag"        # Open-ended multi-step query requiring dynamic planning
    CLARIFICATION = "clarification"          # Ambiguous intent requiring user clarification
    REJECT = "reject"                        # Out-of-scope or policy violation

class RouteDecision(BaseModel):
    route_type: RouteType
    target_agent: str | None = None
    target_workflow_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    reasoning: str
```

### 3.2. FastTriage Architecture

```text
User Request
      │
      ▼
Stage 1: Deterministic Pattern Matcher (Regex / Keyword / Prefix)
  ├─ Matches single domain (e.g., "lịch", "calendar", "họp")? ──► DIRECT_SPECIALIST (CalendarAgent) [0 LLM calls]
  ├─ Matches email query (e.g., "email", "thư", "tin nhắn")? ──► DIRECT_SPECIALIST (CommunicationAgent) [0 LLM calls]
  ├─ Matches document query (e.g., "tra cứu", "quyết định")? ─► DIRECT_SPECIALIST (KnowledgeResearchAgent) [0 LLM calls]
  └─ Matches known workflow trigger? ────────────────────────► STATIC_WORKFLOW [0 LLM calls]
      │
      ▼ (If unclassified or ambiguous)
Stage 2: Lightweight Classifier (Fast Small LLM or Structured Schema)
  ├─ Single specialist intent resolved? ─────────────────────► DIRECT_SPECIALIST
  ├─ Known workflow intent resolved? ─────────────────────────► STATIC_WORKFLOW
  └─ Multi-domain complex intent? ────────────────────────────► SUPERVISOR_DAG (Phase 16)
```

### 3.3. Static Workflow Registry (`app/services/workflow_registry.py`)
Static workflows represent frequently executed, standardized sequences with known dependencies:
- **WF-01: Quick Meeting Follow-up**:
  1. Retrieve calendar event attendees & summary.
  2. Search recent emails from attendees regarding meeting topic.
  3. Draft follow-up summary email.
- **WF-02: Document Search & Briefing**:
  1. Search internal RAG for topic.
  2. Simultaneously search Google Drive for companion attachments.
  3. Synthesize bullet briefing.

Every workflow is registered with:
- `workflow_id`: Unique identifier (e.g. `WF-01`).
- `description`: Human-readable summary of intent.
- `trigger_patterns`: List of intent matchers.
- `graph_factory`: Callable returning the compiled `StateGraph`.

### 3.4. Graph Execution & Parallel `Send` API
Parallel independent nodes are spawned using LangGraph's `Send` primitive:
```python
def dispatch_parallel_branches(state: AssistantState) -> list[Send]:
    sends = []
    if "calendar" in state.pending_domains:
        sends.append(Send("calendar_node", {"query": state.query}))
    if "drive" in state.pending_domains:
        sends.append(Send("drive_node", {"query": state.query}))
    return sends
```
A reducer merges the parallel outputs into `AssistantState` before the synthesis node executes.

---

## 4. Test Matrix & Budget Assertions

### 4.1. Fast Path Assertions (P15-06 & P15-07)
Automated tests assert that common requests route without invoking the Supervisor:
- `"Lịch ngày mai của tôi có gì?"` -> `RouteType.DIRECT_SPECIALIST`, `CalendarAgent`, 0 Supervisor tokens.
- `"Đọc email mới nhất từ anh Nam"` -> `RouteType.DIRECT_SPECIALIST`, `CommunicationAgent`, 0 Supervisor tokens.
- `"Tìm quy định nghỉ phép"` -> `RouteType.DIRECT_SPECIALIST`, `KnowledgeResearchAgent`, 0 Supervisor tokens.

### 4.2. Workflow & Parallelism Tests
- `test_static_workflow_registration_and_match`: Verify workflow matching by trigger pattern.
- `test_workflow_parallel_dispatch`: Verify independent nodes run concurrently via `Send` API.
- `test_state_reducer_merges_parallel_branch_results`: Verify state integrity after parallel fan-in.
- `test_triage_circuit_breaker_on_malformed_input`: Verify graceful fallback to clarification.

---

## 5. Pass Criteria & Exit Gate

The phase is complete and ready for `APPROVED P15` review when:
1. **Zero-Supervisor Fast Path**: 100% of benchmark single-domain queries bypass the Supervisor LLM.
2. **Triage Latency**: Deterministic triage latency <= 10ms; lightweight classifier triage <= 500ms.
3. **Workflow Execution**: At least 2 static workflows compile, execute parallel branches via `Send`, and merge state successfully.
4. **Unit Test Coverage**: >= 85% line coverage on `app/services/triage.py` and `app/services/workflow_registry.py`.
5. **No Regressions**: Full regression suite passes green.
