> Active phase file for P1. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P1`.

# P1 — PROJECT FOUNDATION

## Objective

Create a clean repository and engineering baseline.

## P1 Tasks

### P1-01 Repository structure

Create the target folder structure.

### P1-02 Dependency management

Recommended:

```text
uv
```

Create locked dependency groups.

### P1-03 Typed settings

Use Pydantic Settings.

Cover:

```text
database
redis
LLM providers
Google OAuth
LangSmith
embedding
reranker
ReAct limits
Supervisor limits
LLM-call budgets
timeouts
```

### P1-04 FastAPI

Create:

```text
GET /health
GET /ready
```

### P1-05 Structured logging

Log:

```text
request_id
run_id
component
event
latency
```

Do not log sensitive raw content by default.

### P1-06 Error hierarchy

Implement canonical application errors.

### P1-07 Tests

Configure:

```text
pytest
pytest-asyncio
coverage
```

### P1-08 Quality gates

Configure:

```text
ruff
pyright or mypy
```

Choose one type checker.

## P1 DoD

Project boots with mocked infrastructure.

Lint/typecheck/tests pass.

## P1 Gate

Review Pack → STOP.

---
