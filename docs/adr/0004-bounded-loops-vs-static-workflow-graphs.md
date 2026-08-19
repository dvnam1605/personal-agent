# ADR 0004: Bounded Loops vs Static Workflow Graphs

- **Status**: Approved
- **Date**: 2026-08-17
- **Context**: Autonomous ReAct loops can easily diverge, loop infinitely, or consume excessive tokens if not strictly constrained. Conversely, forcing every multi-step action into a rigid static graph prevents flexible investigation.
- **Decision**:
  1. Allow iterative ReAct loops only within bounded specialist domains for exploratory search and evidence evaluation.
  2. Enforce strict budget limits on every loop (`max_steps = 6`, `max_tool_calls = 8`, timeouts, token limits, no-progress detection).
  3. Require compiled static graphs for recurring, stable workflows with known topology.
- **Consequences**:
  - Positive: Guarantees bounded execution time and predictable cost while preserving open research capabilities.
  - Negative: Developers must monitor trace stability before promoting dynamic workflows to static graphs.
