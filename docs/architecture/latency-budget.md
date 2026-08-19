# Latency & LLM Budget Architecture Contract

## 1. Engineering Principle
> **"No LLM call without an architectural reason."**
> **"Never use an LLM when deterministic code is sufficient."**

Latency and token consumption are tracked as architectural invariants, recorded on every execution run.

---

## 2. Deterministic vs LLM Boundary

### FORBIDDEN to use LLM for:
- Calendar free/busy slot arithmetic.
- Exact contact name or email string matching.
- Exact file name search in Google Drive.
- Gmail provider query formatting (e.g. `after:`, `from:` syntax construction when user input is clear).
- Vector mathematical distance calculation & BM25 text ranking.
- JSON schema validation & Pydantic deserialization.
- Policy evaluation & permission checks.
- Workflow DAG topological sorting & cycle detection.
- Retries, pagination, and exponential backoff.

### ALLOWED to use LLM for:
- Natural language intent triage when heuristics fail.
- Formulating semantic retrieval queries from complex prompts.
- Synthesizing answers from retrieved source evidence.
- Evaluating evidence sufficiency in bounded research loops.
- Resolving highly ambiguous references.
- Dynamic task decomposition in `SupervisorAgent`.

---

## 3. Per-Route Performance & LLM Budgets

| Execution Path | Target LLM Calls | Target Latency (p50) | Target Latency (p95) | Max Tokens |
|---|:---:|:---:|:---:|:---:|
| **Direct Specialist** | 0 – 1 | < 1.5s | < 3.0s | ~2,000 |
| **Specialist ReAct** | 1 – 3 | < 3.5s | < 6.0s | ~8,000 |
| **Known Workflow Graph** | 1 – 2 | < 2.5s | < 5.0s | ~6,000 |
| **Supervisor Multi-Agent DAG** | 3 – 5 | < 6.0s | < 12.0s | ~16,000 |

---

## 4. Telemetry Schema
Every executed run records structured telemetry:

```python
class RunTelemetry(BaseModel):
    run_id: str
    session_id: str
    route_type: RouteType
    agents_activated: list[AgentRole]
    llm_calls: int
    tool_calls: int
    react_steps: int
    supervisor_iterations: int
    parallel_execution_time_ms: float
    total_latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
```
