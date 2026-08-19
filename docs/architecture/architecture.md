# Canonical System Architecture Specification

## 1. End-to-End System Architecture

```text
                                  USER
                                    |
                                    v
                             Context Builder
                                    |
                              +-----+-----+
                              | MemoryGate |
                              +-----+-----+
                                    |
                                    v
                              Fast Triage
                                    |
              +---------------------+----------------------+
              |                     |                      |
              v                     v                      v
       DIRECT SPECIALIST      KNOWN WORKFLOW          OPEN COMPLEX
              |                     |                      |
              v                     v                      v
      Specialist Agent        Static Graph            Supervisor
              |                     |                 Structured Plan
         Direct/ReAct          Specialist Nodes             |
              |                     |                  Dynamic DAG
              +---------------------+----------------------+
                                    |
                                    v
                             Shared Runtime
                                    |
                    +---------------+----------------+
                    |               |                |
                    v               v                v
           CommunicationAgent CalendarAgent KnowledgeResearchAgent
                    |               |                |
               Direct/ReAct     Direct/ReAct      Direct/ReAct
                    |               |                |
              Gmail/Contacts      Calendar       RAG/Drive/Web
                    |               |                |
                    +---------------+----------------+
                                    |
                                    v
                             PolicyEngine
                                    |
                             +------+------+
                             |             |
                            AUTO        APPROVAL
                                    |
                                    v
                                   END
```

---

## 2. Platform Core Subsystems

```text
+-------------------------------------------------------------------------------+
|                             FASTAPI HTTP / WS API                             |
+-------------------------------------------------------------------------------+
| Core Agents:                                                                  |
| - SupervisorAgent                                                             |
| - CommunicationAgent (Gmail, Contacts)                                        |
| - CalendarAgent (Google Calendar)                                             |
| - KnowledgeResearchAgent (pgvector RAG, Drive Search, Web Search)             |
+-------------------------------------------------------------------------------+
| Supporting Services:                                                          |
| - MemoryService & MemoryGate (Short-term Redis + Long-term Postgres)          |
| - CapabilityGate & ToolRegistry (Strict schema & sandbox filtering)           |
| - PolicyEngine & ApprovalManager (HITL pause/resume gate)                     |
| - Retrieval & Ingestion (Parent-Child chunking, BM25 + Vector, Reranker)       |
| - WorkflowRegistry & SkillRegistry (Static Graphs + Dynamic Skills)           |
| - LatencyBudgetManager & Observability (LangSmith + OpenTelemetry)            |
+-------------------------------------------------------------------------------+
| Infrastructure Layer:                                                         |
| - PostgreSQL 16+ (pgvector, metadata, entities, approvals, graph checkpoints) |
| - Redis 7+ (Session cache, rate limits, short-term conversation history)      |
| - External APIs (Google Workspace OAuth2, LLM Providers, Web Search Provider)  |
+-------------------------------------------------------------------------------+
```

---

## 3. Canonical Repository Directory Layout

```text
app/
├── api/                     # FastAPI route handlers and request/response schemas
│   ├── routes/              # Endpoints: chat, workflows, approvals, ingestion, health
│   └── dependencies/        # Auth, DB sessions, Redis clients, service injection
├── agents/                  # The 4 Agent role definitions and prompts
│   ├── supervisor/
│   ├── communication/
│   ├── calendar/
│   └── knowledge_research/
├── harness/                 # Core runtime, orchestration, and graph harness
│   ├── triage/              # Fast triage router and classifier
│   ├── routing/             # Dispatcher to execution paths
│   ├── react/               # Bounded ReAct specialist runner
│   ├── planning/            # Supervisor DAG planner & replanner
│   ├── execution/           # Shared runtime and task runner
│   ├── graphs/              # Static LangGraph workflow graphs
│   ├── workflows/           # Workflow definitions and graph nodes
│   ├── skills/              # Skill parser and dynamic skill executor
│   ├── state/               # Shared runtime state models
│   └── budgets/             # Latency and token budget tracker
├── tools/                   # Concrete tool implementations
│   ├── gmail/
│   ├── contacts/
│   ├── calendar/
│   ├── drive/
│   ├── knowledge/
│   └── web/
├── services/                # Deterministic platform services
│   ├── memory/              # MemoryService, MemoryGate, consolidation
│   ├── entities/            # EntityResolver
│   ├── policy/              # PolicyEngine
│   ├── approvals/           # ApprovalManager (HITL durable state)
│   ├── retrieval/           # Hybrid retriever, reranker, expansion
│   ├── ingestion/           # Parent-Child document chunker & parser
│   └── capabilities/        # CapabilityGate and ToolRegistry
├── infrastructure/          # External integrations and infrastructure
│   ├── db/                  # SQLAlchemy / asyncpg, Alembic migrations
│   ├── redis/               # Redis connection, cache, rate limits
│   ├── google/              # Google OAuth2 client & service wrappers
│   ├── llm/                 # Model provider abstractions & clients
│   └── observability/       # LangSmith tracing, metrics, audit logs
├── domain/                  # Pure domain entities, Pydantic models, enums
│   ├── models/
│   ├── enums/
│   └── errors/
├── core/                    # Core configuration and logging
│   ├── config.py
│   ├── logging.py
│   └── security.py
└── main.py                  # Application entrypoint

skills/                      # Modular Dynamic Skills (Markdown + YAML)
├── meeting-prep/
└── ...

tests/                       # Comprehensive automated test suite
├── unit/
├── integration/
├── evaluation/
├── e2e/
└── fixtures/

docs/                        # System documentation & Decision Records
├── architecture/            # Architectural contracts
├── adr/                     # Architecture Decision Records (ADRs)
└── reviews/                 # Phase Review Packs
```
