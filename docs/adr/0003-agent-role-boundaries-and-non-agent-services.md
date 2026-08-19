# ADR 0003: Agent Role Boundaries and Non-Agent Services

- **Status**: Approved
- **Date**: 2026-08-17
- **Context**: Unbounded agent creation leads to fragmentation, unpredictable prompt behaviors, and complex failure modes.
- **Decision**:
  1. Limit V1 agent roles to exactly four:
     - `SupervisorAgent` (Orchestration & Planning)
     - `CommunicationAgent` (Gmail & Contacts)
     - `CalendarAgent` (Google Calendar)
     - `KnowledgeResearchAgent` (pgvector RAG, Drive Read, Web Search)
  2. Implement all other subsystems (`MemoryService`, `PolicyEngine`, `Retriever`, `EntityResolver`, `ApprovalManager`) as deterministic software services rather than autonomous LLM agents.
- **Consequences**:
  - Positive: High testability, deterministic business logic, clear domain boundaries.
  - Negative: Adding a new agent domain requires formal architecture review and schema registration.
