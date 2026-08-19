# ADR 0005: Skills to Graph Lifecycle and Hardening

- **Status**: Approved
- **Date**: 2026-08-17
- **Context**: Premature hard-coding of static graph workflows creates technical debt and brittle architectures when user behavior and procedural steps are still evolving.
- **Decision**:
  Adopt the 5-stage Skills-to-Graph lifecycle:
  1. `Procedure Idea`
  2. `Dynamic Skill` (`SKILL.md` + `metadata.yaml`)
  3. `Trace & Evaluation Collection` (minimum 50 production/eval traces)
  4. `Stability Assessment` (> 90% structural consistency)
  5. `Compiled Static Graph` (`app/workflows/`)
- **Consequences**:
  - Positive: Rapid iteration on new capabilities using declarative markdown skills without code deployment; rock-solid graph stability once hardened.
  - Negative: Requires an empirical trace evaluation process before workflow hardening.
