# ADR 0009: Prohibition of Peer-to-Peer Agent Calls

- **Status**: Approved
- **Date**: 2026-08-17
- **Context**: Free-form agent-to-agent delegation creates opaque dependency webs, deadlock risks, and non-deterministic trace paths that are difficult to debug and reproduce.
- **Decision**:
  1. Strictly prohibit direct peer-to-peer invocation between specialist agents.
  2. Require all inter-agent data passing and scheduling to flow through the Central Runtime and a typed Shared State / Checkpoint model.
  3. When an agent requires additional data, it must return a typed `CapabilityRequest` to the orchestrator rather than directly invoking a peer.
- **Consequences**:
  - Positive: Clean trace logs, predictable execution order, zero deadlocks, highly reliable distributed checkpointing.
  - Negative: Multi-domain coordination always requires orchestrator involvement.
