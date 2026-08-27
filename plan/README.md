# Personal AI Assistant Plan V4 — Modular Package

This package is the recommended form for Cursor / Antigravity / Codex / Claude Code.

## Read order

1. `MASTER_PLAN.md`
2. One active phase file in `phases/`
3. Relevant ADRs created in the repository during earlier approved phases

Do **not** load every phase file at once.

## Why split the plan?

The monolithic plan is over 5,000 lines. Loading it for every coding turn:
- wastes context,
- dilutes active-phase instructions,
- increases the chance of future-phase leakage,
- makes reviews harder.

The modular package keeps global invariants in one file and implementation details local to each phase.

## Canonical full reference

A monolithic copy is also provided separately as:
`IMPLEMENTATION_PLAN_PERSONAL_AI_ASSISTANT_V4.md`

## Important P9/P10 change

V4 uses:

**structure-aware hierarchical parent–child chunking**

- semantic PARENT chunks: approximately 1200–2000 tokens as a benchmark starting range;
- retrieval CHILD chunks: approximately 350–650 tokens as a benchmark starting range;
- embed/search CHILD by default;
- expand adaptively to `NONE`, `NEIGHBORS`, or `PARENT`;
- separate table-child strategy;
- P10 must benchmark parent-child against the prior single-level structure-aware baseline.
