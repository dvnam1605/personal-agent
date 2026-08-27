# P2 Review Pack — Core Domain Models & Runtime Contracts

## 1. Executive Summary
Phase 2 (P2) establishes the pure, provider-agnostic, strongly-typed domain model layer and runtime contracts for the Personal Multi-Agent AI Assistant.
All models are defined using Pydantic v2 with strict validation and invariants enforced at both construction and mutation time.

---

## 2. Invariant Resolutions & Specifications

### 2.1. Workflow Entry Roots & Graph Integrity (`app/domain/models/workflow.py`)
- Supported multiple entry nodes via `entry_node_ids: list[str] = Field(..., min_length=1)`.
- **Duplicate entry rejection**: Duplicate node IDs in `entry_node_ids` are rejected.
- **Zero incoming edge constraint**: Entry nodes must have zero incoming edges (`in_degree == 0`).
- **Root declaration constraint**: All nodes with zero incoming edges must be declared in `entry_node_ids`.
- **Reachability constraint**: Traversal validation ensures all nodes in `nodes` are reachable from `entry_node_ids`.
- Kahn's algorithm verifies absence of cycles (DAG).

### 2.2. Route Decision Semantics (`app/domain/models/route.py`)
- **Domain requirements**: `domains` field requires at least 1 domain (`min_length=1`).
- `RouteType.KNOWN_WORKFLOW` mandates non-empty `workflow_name`.
- `RouteType.DIRECT_SPECIALIST` mandates exactly 1 domain (`len(domains) == 1`).
- Non-workflow routes require `workflow_name=None`.
- `RouteType.CASUAL_RESPONSE` mandates exactly `[Domain.GENERAL]`.

### 2.3. UTC-Aware Timestamps (`app/domain/models/tool.py`, `evidence.py`, `action.py`)
- Enforced UTC-awareness via `@field_validator("timestamp" / "extracted_at" / "created_at" / "decided_at", mode="after")`. Timezone-naive datetime instances are strictly rejected.

### 2.4. TaskDependency & ExecutionPlan Integration (`app/domain/models/plan.py`)
- **Owning task validation**: `TaskDependency.task_id` is strictly verified against its parent `task.id`.
- **Gating condition evaluation**: Missing condition key or falsy value in context prevents task execution.
- **Assignment validation**: `ExecutionTask` configured with `validate_assignment=True`.

### 2.5. Tool Mutation & Outcome Invariants (`app/domain/models/tool.py`)
- `is_mutation=True` is forbidden with `ActionRiskLevel.READ_ONLY`.
- Write/mutation risk levels mandate `is_mutation=True`.
- `ToolResult.tool_name` is strictly validated against `metadata.tool_name`.
- **Outcome consistency**: `success=True` forbids `error`; `success=False` forbids `output` and mandates `error`.

### 2.6. Skill Step Ordering (`app/domain/models/skill.py`)
- Enforces strictly contiguous 0-based sequential step indices `[0, 1, ..., N-1]`.
- Duplicate, skipped, or out-of-order step indices are rejected.

### 2.7. State & Budget Assignment Safety (`app/domain/models/state.py`, `action.py`, `budget.py`)
- `AssistantState` and `BudgetUsage` configured with `validate_assignment=True` to reject invalid mutations after construction.
- Replaced untyped dictionaries with strongly-typed `ProposedAction` and `ActionApproval` models.
- `BudgetUsage.record_*` methods reject zero and negative increments.

### 2.8. Environment Robustness (`app/core/config.py`)
- Added `@field_validator("debug", mode="before")` on `Settings.debug` to robustly parse ambient string values (such as `DEBUG=release` or `DEBUG=test`) without failing.

---

## 3. Verification & Quality Gates

### 3.1. Automated Test Suite
- **85 automated unit tests** passing with 100% pass rate:
  - `tests/unit/test_config.py` (including `parse_debug` string/boolean tests)
  - `tests/unit/test_logging.py`
  - `tests/unit/test_errors.py`
  - `tests/unit/test_health_routes.py`
  - `tests/unit/test_middleware.py`
  - `tests/unit/domain/test_enums.py`
  - `tests/unit/domain/test_route.py`
  - `tests/unit/domain/test_tool.py`
  - `tests/unit/domain/test_agent.py`
  - `tests/unit/domain/test_evidence.py`
  - `tests/unit/domain/test_plan.py`
  - `tests/unit/domain/test_skill.py`
  - `tests/unit/domain/test_workflow.py`
  - `tests/unit/domain/test_budget.py`
  - `tests/unit/domain/test_state.py`

### 3.2. Coverage & Linting
- **Domain Coverage**: 96–100% across all domain modules and enums.
- **Total Codebase Coverage**: 96%.
- **Ruff**: Passed with 0 errors, 0 warnings.
- **Pyright**: Passed with 0 errors, 0 warnings, 0 informations.
