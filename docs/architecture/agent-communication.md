# Agent Communication Architecture Contract

## 1. Core Rule: Prohibition of Peer-to-Peer Agent Calls
Specialist agents **MUST NOT** directly invoke or message other specialist agents.

### Forbidden Architecture Pattern:
```text
CalendarAgent  ---- (direct call) ---->  CommunicationAgent
      |                                        |
      +------------ (direct call) -----------> KnowledgeResearchAgent
```
*Why Forbidden?*
- Leads to uncontrolled circular dependencies and infinite loops.
- Obscures state transitions and breaks distributed tracing.
- Destroys deterministic reproducibility and makes debugging impossible.

---

## 2. Canonical Communication Model: Shared State & Central Orchestrator

All cross-agent collaboration occurs exclusively through a **Shared State Model** managed by the Orchestration Runtime (Static Graph or Supervisor DAG Executor).

```text
               Supervisor / Graph Runtime
                     |          ^
                     v          |
             [ Shared State / Memory ]
                     |          ^
          +----------+----------+----------+
          |                     |          |
          v                     v          v
   CommunicationAgent     CalendarAgent   KnowledgeResearchAgent
```

### Communication Flow:
1. An agent receives its input state schema from the runtime.
2. The agent executes within its capability boundary and returns a strictly typed result (`TaskResult` or `NeedMoreContext`).
3. The runtime updates the shared execution state (LangGraph channels, persisted by the PostgreSQL checkpointer).
4. The runtime / Supervisor inspects state and activates the next node/agent based on explicit DAG edges or task dependencies.

### Handling Cross-Domain Information Needs:
If a specialist discovers it lacks critical information from another domain, it returns:
```python
class CapabilityRequest(BaseModel):
    required_domain: Domain
    reason: str
    query_hint: str
```
The central orchestrator inspects this request, schedules the corresponding specialist, and resumes execution upon receiving the result.
