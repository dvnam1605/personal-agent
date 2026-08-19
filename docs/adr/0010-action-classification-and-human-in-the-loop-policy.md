# ADR 0010: Action Classification and Human-in-the-Loop Policy

- **Status**: Approved
- **Date**: 2026-08-17
- **Context**: Autonomous execution of sensitive, destructive, or external communications can cause irreversible data loss, schedule corruption, or unintended external commitments.
- **Decision**:
  1. Classify all tool operations into canonical action classes: `READ`, `SAFE_WRITE`, `SENSITIVE_WRITE`, `DESTRUCTIVE`, `EXTERNAL_COMMUNICATION`, `PERMISSION_CHANGE`.
  2. Implement `PolicyEngine` to enforce that sensitive, destructive, and external actions (sending emails, modifying/deleting calendar events, deleting files) trigger Human-in-the-Loop (HITL) approval.
  3. Support durable workflow pause and resume via `ApprovalManager` and Redis/PostgreSQL checkpoints.
- **Consequences**:
  - Positive: Guarantees user safety, prevents data destruction, provides complete transparency over assistant actions.
  - Negative: Requires UI integration for approval cards and durable state checkpointing for long-pending approvals.
