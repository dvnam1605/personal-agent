> Active phase file for P4. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P4`.

# P4 — AGENT / TOOL / CAPABILITY REGISTRY + GATING

## Objective

Create scalable multi-agent architecture without business integrations.

## P4-01 ToolRegistry

Support:

```text
register
get
list
filter_by_capability
```

## P4-02 AgentRegistry

Support:

```text
register
get
list_capabilities
```

## P4-03 Capability declarations

Each agent declares:

```text
name
description
domain
capabilities
allowed_tool_categories
default_execution_mode
```

## P4-04 CapabilityGate

Build runtime tool views.

Example:

```text
CommunicationAgent
 -> Gmail + Contacts only
```

## P4-05 ReadOnly Gate

Support creation of a read-only tool view.

## P4-04A ToolRestriction runtime enforcement

Implement `ToolRestriction` with allow/deny lists at the registry level:

```text
ToolRestriction:
  allow: list[str] | None
  deny:  list[str] | None

ToolRegistry.restrict(restriction) -> ScopedToolView
```

Restricted tools MUST:

1. be removed from the tool schema (model never sees them)
2. reject execution if called by name (defense in depth)
3. log the rejection with the requesting agent identity

## P4-04B Delegation scope metadata

Each agent registration MUST include delegation policy:

```text
delegation_allowed: bool
max_child_depth: int | None
inherits_parent_tools: bool
```

## P4-06 Mutation classification hook

Every mutation tool must declare action class.

## P4-07 Future scale test

Register mock future agents such as:

```text
TaskAgent
TravelAgent
```

ONLY as test doubles.

Verify core registry does not require architecture changes.

Do NOT implement these agents.

## P4-08 Tool Output Spill Policy

When a tool produces an oversized output (exceeding token/byte threshold, e.g. >4KB or >2,000 tokens):

```text
Tool Output > Threshold
 -> Spill to SpillStore (session-scoped file / storage)
 -> Replace inline tool_result with Bounded Preview (head + tail) + locator (`spill://session/output_id`)
 -> Expose slice/fetch tools for the agent to inspect specific offsets if needed
```

Prevents context window explosion during large email list fetching, directory scanning, or large document dumps.

*DeepSeek Harness Reference:*
- Package: `deepseek-harness/packages/spill/` (`spill`, `spill-local`, `spill-policy`)
- Docs: `deepseek-harness/docs/subsystems/spill.md`, `deepseek-harness/docs/tool-execution-pipeline.md`

## P4 Tests

- duplicate tool
- duplicate agent
- capability filtering
- forbidden tool unavailable
- forbidden tool rejects execution by name
- read-only registry
- future mock agent registration
- delegation scope metadata validation
- tool output spill triggers above threshold and preserves locator

## P4 Gate

STOP.

---
