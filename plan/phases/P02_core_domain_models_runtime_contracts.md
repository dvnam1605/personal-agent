> Active phase file for P2. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P2`.

# P2 — CORE DOMAIN MODELS + RUNTIME CONTRACTS

## Objective

Define stable typed contracts before infrastructure integrations.

## P2-01 AssistantState

Minimum:

```text
run_id
user_id
request
normalized_request
route_decision
goal
entities
active_skill
active_workflow
plan
task_results
evidence
missing_information
proposed_actions
approvals
iteration
react_steps
tool_call_count
llm_call_count
status
errors
```

## P2-02 RouteDecision

Model:

```text
domains
complexity
route_type
workflow_name
confidence
reason_code
```

## P2-03 Agent contracts

Implement:

```text
AgentRequest
AgentResult
TaskResult
NeedMoreContext
CapabilityRequest
DelegationContext        # frozen scope/policy for delegated execution
DelegationResult         # extends AgentResult with needs_approval flag
```

## P2-04 Tool contracts

Implement:

```text
ToolDefinition
ToolInput
ToolContext
ToolResult
ToolExecutionMetadata
```

## P2-05 Evidence

Canonical evidence must preserve source identity.

## P2-06 Plan contracts

Implement:

```text
ExecutionPlan
ExecutionTask
TaskDependency
```

## P2-07 Skill contracts

Implement:

```text
SkillDefinition
SkillStep
SkillConstraints
SkillCompletionCriteria
```

## P2-08 Workflow contracts

Implement:

```text
WorkflowDefinition
WorkflowNode
WorkflowEdge
WorkflowExecutionResult
```

## P2-09 Budget contracts

Implement:

```text
ExecutionBudget
BudgetUsage
BudgetViolation
```

Fields may include:

```text
max_llm_calls
max_tool_calls
max_react_steps
max_supervisor_iterations
max_delegation_depth          # chain depth cap (default 3)
timeout
```

## P2 Tests

- model serialization
- invalid status
- invalid plan dependency
- workflow definition validation
- skill validation
- budget validation

## P2 Gate

STOP after Review Pack.

---
