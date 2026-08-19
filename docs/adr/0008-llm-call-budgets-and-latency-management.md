# ADR 0008: LLM Call Budgets and Latency Management

- **Status**: Approved
- **Date**: 2026-08-17
- **Context**: LLM agent systems often suffer from runaway execution loops, unmonitored costs, and sluggish user experience when not governed by latency and call budgets.
- **Decision**:
  1. Enforce the invariant: "Never use an LLM when deterministic code is sufficient" (e.g. slot math, exact contact lookup, vector distance, JSON validation).
  2. Set explicit per-route budgets (Direct Specialist: 0-1 calls, < 2s; Known Workflow: 1-2 calls, < 3s; Supervisor DAG: 3-5 calls, < 6s).
  3. Instrument every run with `RunTelemetry` measuring latency, call counts, token usage, and cost.
- **Consequences**:
  - Positive: High responsiveness, clear cost boundaries, immediate alerting on runaway regressions.
  - Negative: Requires writing and maintaining deterministic algorithmic logic for scheduling and lookups.
