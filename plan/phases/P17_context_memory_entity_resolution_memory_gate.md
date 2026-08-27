> Active phase file for P17. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P17`.

# P17 — CONTEXT + MEMORY + ENTITY RESOLUTION + MEMORY GATE

## Objective

Add personal continuity without adding latency to every request.

## Memory types

```text
Conversation
Working
Entity
Preference
Episodic
```

## P17-01 EntityResolver

Entities:

```text
PERSON
DOCUMENT
EMAIL
THREAD
EVENT
TOPIC
PROJECT
ORGANIZATION
```

## P17-02 Conversation references

Support:

```text
"email đó"
"file kia"
"ba tài liệu trên"
"cuộc họp vừa nói"
```

## P17-03 Preference memory

Store useful stable preferences.

## P17-04 Episodic memory

Store meaningful confirmed events, not every turn.

## P17-05 MemoryGate

Decide whether memory retrieval is needed.

## P17-06 Context Builder

Retrieve only relevant memory.

## P17-07 Background consolidation

Use worker/queue where appropriate.

Do not block response for optional consolidation.

## P17-08 Token-Pressure Context Compaction

Proactive context management before hitting model context limits:
- **Pressure Trigger**: When token usage reaches threshold (e.g. 75%-80% of context window), trigger compaction at `pre-step` before dispatching to LLM.
- **Two-Stage Execution**:
  1. *Stage 1 (Model-free)*: Run `ToolResultPruner` (P17-09). Remeasure tokens.
  2. *Stage 2 (LLM Summarization)*: If still under pressure, summarize historical turns into a durable `CompactionCheckpoint` and replace the shadowed range in conversation surface.

## P17-09 Model-Free Tool-Result Pruner

Prune historical oversized tool results from older conversation turns without calling an LLM:
- Replaces raw JSON / search responses / file contents of completed past turns with compact status summaries (`[Tool 'search_drive' returned 15 files - result pruned]`).
- Reclaims 50-80% of wasted tokens instantly with zero latency/cost.

## P17-10 Tool-Pairing Invariant

During compaction or pruning, strict validation ensures:
- Never orphan a `tool_call` without its corresponding `tool_result` (and vice-versa).
- Compaction slice boundaries must land on clean balanced turn / tool boundaries (`toolPairingBalancedBefore` / `toolPairingBalancedAfter`).

*DeepSeek Harness Reference:*
- Package: `deepseek-harness/packages/compaction/` (`compaction`, `compaction-basic`, `compaction-tool-result-pruner`)
- Docs: `deepseek-harness/docs/subsystems/compaction.md`, `deepseek-harness/docs/subsystems/session.md`

## Tests

- memory not invoked for trivial unrelated request
- "Nam" resolution
- ambiguous Nam
- "ba tài liệu đó"
- preference usage
- stale/incorrect memory rejection
- token pressure triggers Stage 1 tool-result pruning without LLM call
- tool-pairing boundary invariant maintained during compaction (no orphan tool_call/tool_result)
- compaction summary preserves essential context across long conversations

## Gate

STOP.

---
