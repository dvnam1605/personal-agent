# ADR 0006: Strict Capability and Tool Gating

- **Status**: Approved
- **Date**: 2026-08-17
- **Context**: Exposing all system tools to every agent increases prompt token overhead, degrades model focus, and introduces severe prompt-injection and unauthorized mutation risks.
- **Decision**:
  Enforce strict capability gating via `CapabilityGate`:
  1. Filter tool definitions bound to an agent's LLM context strictly by agent role.
  2. Maintain a sandbox read-only view for research/knowledge extraction workflows.
  3. Treat all external content (emails, docs, web results) as untrusted strings wrapped in isolation delimiters.
- **Consequences**:
  - Positive: Robust defense-in-depth; agents cannot invoke unauthorized or cross-domain tools.
  - Negative: Tool schemas and capability bindings must be maintained in the central `ToolRegistry`.
