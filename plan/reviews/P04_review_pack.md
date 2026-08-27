# P4 Review Pack — Agent / Tool / Capability Registry + Gating

## 1. Executive Summary

P4 adds first-party capability metadata and least-privilege runtime tool views without adding provider integrations or concrete business agents. Tool and agent declarations are registered independently, exposed through immutable snapshots, and filtered before an activation can retrieve a tool by name.

## 2. Implemented Scope

### 2.1. Capability metadata

- Added canonical `ActionClass` values from the architecture contract:
  `READ`, `SAFE_WRITE`, `SENSITIVE_WRITE`, `DESTRUCTIVE`,
  `EXTERNAL_COMMUNICATION`, and `PERMISSION_CHANGE`.
- Added `ExecutionMode` values for `DIRECT` and `BOUNDED_REACT`.
- Extended `ToolDefinition` with:
  - optional tool `category`;
  - fine-grained `capabilities`;
  - optional `action_class` preserved for P2 construction compatibility.
- Added `AgentDefinition` with the required declaration fields:
  `name`, `description`, `domain`, `capabilities`,
  `allowed_tool_categories`, and `default_execution_mode`.

### 2.2. ToolRegistry (`app/tools/registry.py`)

- Supports `register`, `get`, `list`, and `filter_by_capability`.
- Rejects duplicate tool names.
- Supports exact, hierarchical, and glob capability/category matching.
- Enforces mutation classification at registration: mutation tools must provide a non-`READ` action class.
- Supports an explicit mutation-classifier hook for adapters that derive the class from provider metadata.
- Returns defensive copies so callers cannot mutate registry state through Pydantic list fields.

### 2.3. AgentRegistry (`app/agents/registry.py`)

- Supports registration, lookup, listing, duplicate detection, and per-agent/all-agent capability catalogs.
- Stores declarations only; no future agent implementation was added.

### 2.4. CapabilityGate (`app/services/capability_gate.py`)

- Builds agent-specific immutable `ToolRegistryView` snapshots.
- Applies `allowed_tool_categories` as the primary exposure boundary.
- Applies explicit tool capability labels as an additional restriction.
- Supports request-level narrowing that cannot widen an agent's declared access.
- Provides read-only views that omit mutation tools before retrieval.
- An unavailable tool is absent from the view and raises `NotFoundError` even when requested by name.

### 2.5. Read-only registry view

`ToolRegistryView` and its `ReadOnlyToolRegistry` alias expose only the captured snapshot. Registration is rejected, and `as_read_only()` can only narrow access further.

## 3. Verification

### Automated tests

- **140 tests passed**.
- Added coverage for:
  - duplicate tool and agent registration;
  - capability filtering;
  - action-class enforcement;
  - read-only views;
  - defensive copies;
  - forbidden mutation lookup by a research mock;
  - request-level narrowing;
  - future mock agent registration (`TravelAgent`) without implementing the agent.

### Quality gates

- Ruff: passed with 0 errors.
- Pyright: passed with 0 errors, 0 warnings, 0 informations.
- Full pytest suite: **146 passed**; one existing SQLite `ResourceWarning` remains in the Redis fallback test.

## 4. Review Findings and Fix Verification

### HIGH

**0 open findings.** No capability-gate bypass or mutation exposure remained after review.

### MEDIUM

**M-01 — Explicit category bypass via tool-name namespace — FIXED.**

The gate previously matched both an explicit category and the tool-name prefix. A tool named `drive.delete` with an explicit `admin` category could therefore be exposed to a `drive` agent. Explicit categories are now authoritative; the name prefix is only used when category metadata is absent. A regression test covers this case.

**M-02 — Undocumented `all` wildcard — FIXED.**

The matcher previously treated the literal category `all` as full access. Only the explicit `*` pattern is now a wildcard; `all` is an ordinary category name.

**M-03 — Non-canonical tool identities and labels — FIXED.**

Tool names, descriptions, categories, and capability labels are now trimmed, blank-checked, and capability labels are de-duplicated during model validation. This prevents semantically duplicate registry keys and inconsistent lookup behavior.

### LOW

**L-01 — Registry mutation is not synchronized — ACCEPTED for P4.**

Registries are mutable configuration containers, while views are intentionally immutable snapshots. P4 does not introduce runtime hot-registration or concurrent registry updates; startup registration is the supported lifecycle. Dynamic registration synchronization can be revisited only if a later phase requires it.

## 5. Security / Architecture Self-Review

- Capability exposure is enforced by a runtime view, not by a prompt instruction.
- Research/read-only views cannot retrieve mutation tools by name.
- Mutation classification is required at the registry boundary.
- No Gmail, Calendar, Drive, OAuth, PolicyEngine, or concrete agent business logic was added.
- No P5 or later implementation was started.

## 6. Compatibility Note

`ToolDefinition.action_class` remains optional during model construction so existing P2 domain-contract callers remain valid. `ToolRegistry.register()` is the authoritative P4 boundary and normalizes read-only tools to `ActionClass.READ` while rejecting unclassified mutations.

## 7. Final Verdict

**PASS — HIGH: 0 open, MEDIUM: 0 open, LOW: 1 accepted.**

All required P4 behavior is implemented and verified. The phase remains bounded to registries, declarations, gating, and test doubles.

## 8. Gate Status

**WAITING FOR USER REVIEW — P4**

Do not begin P5 until the user explicitly sends `APPROVED P4`.
