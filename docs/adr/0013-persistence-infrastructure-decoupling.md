# ADR 0013: Substrate Isolation & Persistence Decoupling from Services

- **Status**: Approved (Transitional Exemption for v1.0, Target Migration v1.1)
- **Date**: 2026-09-09
- **Supersedes**: Clarifies §18A.5 substrate boundaries in `MASTER_PLAN.md`.
- **Context**:
  Section 18A.5 of the Master Plan specifies strict layer isolation:
  1. `app/domain/`: Pure domain models, value objects, and ports using only Python stdlib and Pydantic (0 framework imports).
  2. `app/harness/`: The sole package authorized to import the LangGraph substrate (`StateGraph`, checkpointers, `interrupt`, `Send`).
  3. `app/services/`: Application use cases, business logic, workflows, policy engines, and coordinators.
  
  During the pre-v1.0 audit, an AST validation check confirmed 0 `langgraph` framework imports across `domain/` and `services/`. However, audit inspection identified 53 direct imports of `sqlalchemy`, `httpx`, and `redis` across 12 files in `app/services/` (e.g. `services/approvals.py`, `services/run_persistence.py`, `services/question_plane.py`, `services/google_auth.py`, `services/consumed_store.py`).
  
  Strict Hexagonal/Clean Architecture dictates that infrastructure protocols (ORM, HTTP client, cache driver) should live in `app/infrastructure/` behind domain ports, rather than being directly coupled in application services.

- **Decision**:
  1. **Clarification of §18A.5 Automated Enforcement**: The automated AST rule strictly asserts:
     *"Zero Agent Orchestration Framework (LangGraph, CrewAI, AutoGen, LlamaIndex) imports outside app/harness/"*. This invariant is 100% verified with 0 violations across all 121 application files.
  2. **Transitional Exemption for v1.0**: The existing direct imports of `sqlalchemy`, `httpx`, and `redis` in `app/services/` are granted an approved transitional exemption for the initial v1.0 release, as all business logic boundaries, security gates, and policy enforcements are fully operational.
  3. **Target Decoupling Roadmap (v1.1)**:
     - Introduce persistence repository adapters in `app/infrastructure/db/` for `ApprovalRepository`, `RunStateRepository`, and `QuestionRepository`.
     - Introduce HTTP client adapters in `app/infrastructure/http/` for Google OAuth and LLM synthesis.
     - Move Redis driver access in `services/consumed_store.py` down into `app/infrastructure/redis/`.
     - Refactor `app/services/` to depend solely on domain ports (`app/domain/ports/`), achieving true 0-framework purity in both `domain/` and `services/`.

- **Consequences**:
  - Positive: Guarantees zero agent framework leakage into services/domain while shipping v1.0 on schedule.
  - Positive: Clear roadmap for decoupling persistence and external protocols in v1.1.
  - Neutral: Automated CI checks distinguish between orchestration framework leakage (hard blocker) and infrastructure driver coupling (tracked architectural debt).
