# Personal AI Assistant V1 — Scope Contract

## 1. Goal & Product Vision
Build a production-grade Personal AI Assistant designed for personal productivity and future enterprise scaling. The assistant operates under a capability-bounded multi-agent architecture with hybrid execution paths (Direct Specialist, Known Static Workflow Graphs, and Open-ended Supervisor DAGs).

## 2. V1 Supported Features
1. **Natural Language Understanding & Triage**: Intent classification and deterministic routing to the optimal execution path.
2. **Communication (Gmail & Google Contacts)**:
   - Search, read, summarize emails and threads.
   - Draft, reply, send emails (send subject to policy approval).
   - Label, star, archive, and trash messages.
   - Resolve contacts, nicknames, and email addresses deterministically.
3. **Calendar Management (Google Calendar)**:
   - List and search events.
   - Deterministic free/busy calculation and slot search.
   - Create, update, and delete calendar events (writes subject to policy approval).
4. **Drive & File Operations (Google Drive)**:
   - Search, list, and download files.
   - Create folders, upload, rename, and move files.
   - Read and manage basic file permissions.
5. **Document Ingestion & Knowledge Base**:
   - Ingest large PDF and DOCX document collections.
   - Structure-aware Hierarchical Parent–Child chunking (benchmark: ~1200–2000 token parents, ~350–650 token children, dedicated table-child strategy).
   - Embedding generation and vector indexing.
6. **Retrieval & RAG Engine**:
   - Hybrid retrieval (pgvector cosine similarity + Full Text Search BM25).
   - Cross-encoder reranking.
   - Adaptive parent/neighbor chunk expansion.
   - Precise citation preservation and verifiable source attribution.
7. **Research & Multi-Source Comparison**:
   - Internal knowledge retrieval combined with Drive search and external Web search.
   - Structured synthesis and factual comparison.
8. **Memory & Context Management**:
   - Short-term conversation history and thread state in Redis.
   - Long-term memory (user preferences, facts, entities) in PostgreSQL.
   - MemoryGate to avoid redundant memory retrieval.
   - Asynchronous off-critical-path memory consolidation.
9. **Safety, Policy & Human-in-the-loop (HITL)**:
   - Strict PolicyEngine gating every tool execution.
   - ApprovalManager for sensitive and destructive operations.
   - Durable pause/resume workflows for approval states.
10. **Observability & Budgeting**:
    - Per-run telemetry: latency, LLM calls, tool calls, token usage, estimated cost.
    - Integration with LangSmith and structured local run audits.

## 3. Supported Integrations
- **Google Workspace APIs**: Gmail API (v1), Google People API (Contacts v1), Google Calendar API (v3), Google Drive API (v3).
- **Database & Storage**: PostgreSQL 16+ with `pgvector` for durable state, audit, and graph checkpoints; Redis 7+ for caching, rate limits, and short-term conversation history.
- **LLM / Embeddings**: Primary reasoning LLMs (e.g. Claude 3.5 Sonnet / GPT-4o), fast classifiers (e.g. GPT-4o-mini / Haiku), embedding models (e.g. AITeamVN/Vietnamese_Embedding - 1024 dims), and cross-encoder rerankers (e.g. namdp-ptit/ViRanker).
- **Search Provider**: Tavily / Serper / DuckDuckGo for web search.

## 4. Agent Inventory (Exactly 4 Roles in V1)
1. **SupervisorAgent**: Coordinates complex, cross-domain, multi-step requests requiring dynamic decomposition and DAG execution.
2. **CommunicationAgent**: Specializes in email threads, drafts, messages, and contact lookups.
3. **CalendarAgent**: Specializes in schedule querying, availability calculation, and calendar updates.
4. **KnowledgeResearchAgent**: Specializes in internal RAG, document parsing, Drive search, web search, and evidence synthesis.

## 5. Non-Agent Service Inventory
The following subsystems are explicitly deterministic software services, **not** LLM agents:
- `MemoryService` / `MemoryGate`
- `EntityResolver`
- `PolicyEngine` / `ApprovalManager`
- `Retriever` / `Reranker` / `EmbeddingService`
- `DocumentParser` / `IngestionPipeline`
- `WorkflowRegistry` / `SkillRegistry` / `ToolRegistry`
- `CapabilityGate`
- `LatencyBudgetManager` / `TraceAuditService`

## 6. Supported Read vs. Write Capabilities
| Capability Category | Operations | Execution Mode | Policy Default |
|---|---|---|---|
| **Read / Query** | Search/read Gmail, Calendar, Drive, Internal RAG, Web | Direct / ReAct | AUTO |
| **Safe Write** | Create email drafts, add reminder notes | Direct / Static Graph | AUTO |
| **Sensitive Write** | Create/Update Calendar events, Send emails, Move/Rename Drive files | Direct / Graph / ReAct | APPROVAL / Configurable |
| **Destructive / Security** | Delete Calendar events, Trash/Delete emails, Delete Drive files, Modify permissions | Direct / Graph / ReAct | STRICT APPROVAL |

## 7. Scaling Assumptions & Invariants
- Single-user personal assistant first, with clean database schemas and multi-tenant ready boundaries.
- Postgres + pgvector handles up to 500,000 document chunks efficiently without requiring standalone vector databases.
- Multi-agent is a capability boundary: single-domain requests never invoke the supervisor or multiple agents.

## 8. Non-Goals (Out of Scope for V1)
- Slack, Microsoft Teams, Jira, Linear, GitHub project integrations.
- CRM, HR, Travel, Finance/Accounting specialized agents.
- Voice/STT/TTS interfaces.
- Multi-tenant enterprise RBAC organizations.
- Graph databases (e.g., Neo4j) or Kafka streaming clusters.
- Autonomous unapproved destructive mutations or financial transactions.
- Unbounded ReAct loops and unbounded supervisor replanning.
