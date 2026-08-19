# Execution Paths Architecture Contract

## 1. Overview
A core invariant of this architecture is:
> **"Multi-agent is a capability boundary, not an execution requirement."**

To eliminate orchestration overhead, latency, and unnecessary token burn, the system routes requests across three distinct execution tiers.

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
          |                   |              Dynamic Plan
          |             Specialist Nodes           |
          |                   |              Dynamic DAG
          +-------------------+--------------------+
                              |
                              v
                           RESULT
```

---

## 2. The Three Execution Paths

### Path A: Single-Domain Specialist (`DIRECT_SPECIALIST`)
- **Trigger**: The request pertains to a single specialist domain (Communication, Calendar, or Knowledge/Research) and does not require complex multi-step cross-domain coordination.
- **Workflow**:
  1. Fast Triage identifies domain and selects specialist.
  2. Specialist runs in either:
     - **Direct Mode**: Deterministic tool invocation + direct formatting (e.g. "What is my schedule tomorrow?" -> `calendar.list_events`).
     - **Bounded ReAct Mode**: Multi-step iterative tool search within its domain (e.g. "Find recent emails from Nam about RAG and summarize the conclusion" -> contacts lookup, thread search, reading messages, synthesis).
  3. Returns response directly.
- **Budget**: 0–1 LLM calls (Direct) or 1–3 LLM turns (ReAct). Latency target: < 2–3s (Direct), 2–6s (ReAct).

### Path B: Known Workflow Graph (`KNOWN_WORKFLOW`)
- **Trigger**: The request matches a known, stable, predefined procedure involving multiple tools or specialists (e.g., "Prepare meeting brief for tomorrow", "Daily Morning Briefing").
- **Workflow**:
  1. Fast Triage matches pattern to `WorkflowRegistry`.
  2. Pre-compiled static LangGraph workflow executes deterministic data fetching nodes in parallel.
  3. Specialist nodes execute domain logic.
  4. Final synthesis node produces consolidated brief.
- **Characteristics**: Zero supervisor planning overhead; deterministic control flow; parallel independent branch execution.
- **Budget**: 1–2 LLM synthesis calls; parallelized deterministic fetches.

### Path C: Open Complex Multi-Domain (`SUPERVISOR`)
- **Trigger**: Multi-domain request with semantic dependencies and novel task structures that cannot be matched to known workflows.
- **Workflow**:
  1. Fast Triage detects multi-domain intent without workflow match.
  2. `SupervisorAgent` decomposes request into structured JSON DAG with tasks, capabilities, and dependencies.
  3. Dynamic Graph executor runs independent tasks in parallel and dependent tasks sequentially.
  4. Specialists execute sub-tasks within their gated capabilities.
  5. Bounded replanning occurs only upon task failure or missing context.
  6. Final Synthesis combines sub-task outputs into user response.
- **Budget**: Supervisor Plan LLM call + Specialist LLM calls + Final Synthesis call.

---

## 3. Fast Triage Specification
The triage layer is lightweight and deterministic-first:

```python
class RouteType(str, Enum):
    DIRECT_SPECIALIST = "DIRECT_SPECIALIST"
    KNOWN_WORKFLOW = "KNOWN_WORKFLOW"
    SUPERVISOR = "SUPERVISOR"
    CASUAL_RESPONSE = "CASUAL_RESPONSE"

class RouteDecision(BaseModel):
    domains: list[Domain]
    complexity: Complexity
    route_type: RouteType
    workflow_name: str | None = None
    target_agent: AgentRole | None = None
    confidence: float
```

### Triage Priority Order:
1. **Deterministic / Regex match**: Direct keyword / intent matches.
2. **Known Workflow registry match**: Match against registered workflow triggers.
3. **Lightweight classifier**: Fast small model call if ambiguous.
4. **Supervisor fallback**: Only invoked for genuine open complexity.
