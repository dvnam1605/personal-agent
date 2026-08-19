# ADR 0007: Memory Gate and Background Consolidation

- **Status**: Approved
- **Date**: 2026-08-17
- **Context**: Querying and injecting complete memory context on every single turn adds unnecessary database/vector latency and pollutes the prompt for simple, unambiguous commands.
- **Decision**:
  1. Introduce `MemoryGate` before execution to evaluate whether memory retrieval is required (triggered only by pronoun ambiguity, user preference needs, or explicit history continuation).
  2. Move long-term memory extraction and consolidation entirely to asynchronous background workers off the critical interactive response path.
- **Consequences**:
  - Positive: Fast, deterministic sub-second response times for self-contained user requests; zero latency penalty for memory maintenance.
  - Negative: Memory consolidation occurs with slight eventual consistency delay.
