# Loop vs Graph Architecture Contract

## 1. Core Philosophy
Autonomous agent execution patterns follow two distinct paradigms:
1. **Dynamic Bounded Loops (ReAct)**: Iterative reasoning where each step depends on previous observations.
2. **Deterministic Graphs (LangGraph Workflows)**: Pre-compiled execution DAGs with fixed topology, branching, and parallelism.

The system applies the principle:
> **"Use a loop when the next step is uncertain; use a graph when the workflow shape is known."**

---

## 2. When Bounded Loops (ReAct) Are Allowed

### Use Cases:
- **Open-Ended Specialist Research**: When the agent must query, inspect evidence sufficiency, reformulate queries, and explore multiple sources.
- **Ambiguous Entity / Thread Search**: When searching for an email or person requires inspecting top results, pagination, and checking related threads.
- **Dynamic Problem Decomposition**: The Supervisor determining subsequent subtasks based on interim task completion.

### Mandatory Invariants for ReAct Loops:
Every loop execution **MUST** be strictly bounded with concrete limits:
```python
class ReActBudgetConfig:
    max_steps: int = 6           # Max reasoning iterations
    max_tool_calls: int = 8      # Max total tool executions
    timeout_seconds: float = 30.0
    token_budget: int = 8000
    stop_on_no_progress: bool = True
```

### Loop Termination Conditions:
A ReAct loop MUST immediately terminate when:
1. The specialist determines evidence is sufficient to answer the prompt.
2. A terminal tool returns final output.
3. Repetitive tool calls with identical arguments are detected (no-progress detector).
4. `max_steps` or `max_tool_calls` limit is reached.
5. Timeout or token budget is exhausted.
6. A policy denial occurs.

---

## 3. When Static Graphs Are Preferred

### Use Cases:
- **Repeatable Multi-Step Workflows**: Procedures executed frequently with known data flow (e.g. `meeting-prep`, `daily-briefing`).
- **Parallel Independent Data Fetching**: Simultaneous queries across Calendar, Gmail, and Knowledge bases.
- **Strict Verification Pipelines**: Workflows requiring deterministic post-processing, schema validation, and policy checks.

### Advantages over Dynamic Loops:
- **Predictable Latency & Cost**: Minimum generative LLM calls; parallel asynchronous execution.
- **Deterministic Observability**: Every node state transition is explicit and debuggable.
- **Zero Hallucinated Flow**: Eliminates agent drift or forgotten steps.

---

## 4. Hardening Criteria: From Dynamic Loop to Static Graph

A procedure MUST NOT be hardcoded into a graph on first implementation. It must follow the hardening lifecycle:

```text
Dynamic Loop / Skill (Exploratory)
          |
          v
  Trace Collection & Evaluation (> 50 runs)
          |
          v
  Stability Analysis (Consistent node sequence & low variance)
          |
          v
  Compiled Static LangGraph (Production Hardened)
```

### Hardening Evidence Requirements:
1. **Frequency**: The workflow is invoked regularly by users.
2. **Trace Stability**: Traces demonstrate that the sequence of tool invocations is stable (> 90% of runs follow the same structural path).
3. **Evaluation Benchmark**: The static graph matches or exceeds the quality score of the dynamic loop while cutting p95 latency by at least 30%.
