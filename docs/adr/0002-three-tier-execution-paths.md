# ADR 0002: Three-Tier Execution Paths

- **Status**: Approved
- **Date**: 2026-08-17
- **Context**: Monolithic router-supervisor pipelines add unacceptable latency (often 10s+) and token cost for simple queries like checking tomorrow's calendar or reading an email.
- **Decision**:
  Implement three distinct execution paths routed by a lightweight `FastTriage` layer:
  1. `DIRECT_SPECIALIST`: Routes directly to a single specialist running in Direct or Bounded ReAct mode.
  2. `KNOWN_WORKFLOW`: Routes to pre-compiled static LangGraph workflows with parallel execution nodes.
  3. `SUPERVISOR`: Invokes `SupervisorAgent` only for genuinely open, complex multi-domain queries requiring dynamic DAG planning.
- **Consequences**:
  - Positive: Simple queries finish in < 2 seconds with 0–1 LLM calls; supervisor overhead is incurred only when necessary.
  - Negative: Requires accurate triage heuristics and clear route decision schemas.
