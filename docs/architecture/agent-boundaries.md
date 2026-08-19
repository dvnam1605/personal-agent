# Agent Boundaries and Non-Agent Subsystems

## 1. Overview
To prevent role bleed, circular delegations, and debugging complexity, V1 strictly confines autonomous agent reasoning to **4 specialist roles**. All other platform features are deterministic, testable software services.

---

## 2. Agent Responsibilities & Invariants

```text
                     AgentRegistry
                           |
       +-------------------+--------------------+
       |                   |                    |
       v                   v                    v
CommunicationAgent   CalendarAgent    KnowledgeResearchAgent
 (Gmail, Contacts)     (Calendar)      (RAG, Drive, Web)
       ^                   ^                    ^
       |                   |                    |
       +-------------------+--------------------+
                           |
                     SupervisorAgent
                (Orchestration & DAG Plan)
```

### 2.1. SupervisorAgent
- **Domain**: High-level task decomposition, execution plan generation, DAG synthesis, and dynamic replanning.
- **Allowed Operations**:
  - Analyze open-ended multi-domain user goals.
  - Produce validated task graph JSON (`tasks`, `capability`, `depends_on`, `instruction`).
  - Synthesize sub-task outputs into final user answers.
- **Strict Invariants**:
  - **MUST NOT** be invoked for single-domain requests.
  - **MUST NOT** directly execute low-level provider tools (e.g. `gmail.send` or `calendar.create_event`).
  - **MUST NOT** enter unbounded replanning loops (max replans = 2).

### 2.2. CommunicationAgent
- **Domain**: Email messages, threads, labels, drafts, and contact resolution.
- **Allowed Tools**: `gmail.*`, `contacts.*`.
- **Modes**:
  - Direct: single email retrieval or simple draft creation.
  - Bounded ReAct: contact resolution -> thread search -> candidate review -> summary.
- **Strict Invariants**:
  - **MUST NOT** execute calendar scheduling or search document knowledge bases.
  - All write mutations (`gmail.send`, `gmail.trash`) must pass through PolicyEngine.

### 2.3. CalendarAgent
- **Domain**: Schedule inquiries, availability checks, event creation, updates, and cancellations.
- **Allowed Tools**: `calendar.*`, `contacts.resolve_person`.
- **Modes**:
  - Direct: event lookup, simple schedule presentation.
  - Bounded ReAct: conflict resolution, multi-participant availability analysis.
- **Strict Invariants**:
  - **MUST** use deterministic slot arithmetic and free/busy computation utilities instead of LLM time math.
  - **MUST NOT** read internal knowledge bases or dispatch emails.

### 2.4. KnowledgeResearchAgent
- **Domain**: Document retrieval (RAG), internal PDF/DOCX evidence, Google Drive file search/reading, and external web research.
- **Allowed Tools**: `knowledge.search`, `knowledge.read_chunk`, `drive.search`, `drive.read`, `drive.download`, `web.search`.
- **Modes**:
  - Direct: direct document lookup or citation verification.
  - Bounded ReAct: multi-query vector/FTS search -> rerank -> chunk expansion -> web search fallback -> synthesis.
- **Strict Invariants**:
  - **MUST NOT** receive write/destructive tools (e.g. `gmail.send`, `calendar.delete`, `drive.delete`).
  - Must preserve citations for every generated factual claim.

---

## 3. Explicit Non-Agent Subsystems
The following components are implemented as standard Python classes and FastAPI services, not autonomous agents:

| Subsystem | Implementation Nature | Reason |
|---|---|---|
| `MemoryService` / `MemoryGate` | Deterministic DB/Redis query & heuristic filter | Predictable latency, avoid hallucinated memory recall |
| `EntityResolver` | Exact match + DB index + fuzzy ratio + small fallback model | High precision, low latency |
| `PolicyEngine` / `ApprovalManager` | Rule-based evaluator + persistent approval states | Deterministic security gating |
| `Retriever` & `Reranker` | Hybrid pgvector + FTS BM25 + Cross-Encoder | Mathematical vector scoring, zero agentic overhead |
| `IngestionPipeline` | Hierarchical chunker + metadata extractor + embedder | Batch pipeline processing |
| `WorkflowRegistry` / `SkillRegistry` | In-memory registry & validator | Fast lookup |
| `LatencyBudgetManager` | Telemetry tracker and token accountant | Precise metric recording |
