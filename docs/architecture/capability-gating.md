# Capability Gating Architecture Contract

## 1. Core Principle
This architecture enforces strict **Capability Gating**:
> **"Agents must never see or access tools outside their defined operational boundary."**

Security and reliability are guaranteed at the runtime schema level (filtering tool definitions provided in the LLM prompt and API binding), rather than relying purely on negative prompt instructions (e.g. "please do not call gmail tools").

---

## 2. Tool Exposure Matrix

| Tool Category | Specific Tools | CommunicationAgent | CalendarAgent | KnowledgeResearchAgent | SupervisorAgent |
|---|---|:---:|:---:|:---:|:---:|
| **Gmail Read** | `gmail.search`, `gmail.read`, `gmail.get_thread` | **YES** | NO | NO | NO |
| **Gmail Safe Write** | `gmail.create_draft` | **YES** | NO | NO | NO |
| **Gmail Mutate** | `gmail.send`, `gmail.archive`, `gmail.trash` | **YES** (Policy Gated) | NO | NO | NO |
| **Contacts** | `contacts.search`, `contacts.resolve_person` | **YES** | **YES** | NO | NO |
| **Calendar Read** | `calendar.list_events`, `calendar.get_freebusy` | NO | **YES** | NO | NO |
| **Calendar Mutate** | `calendar.create_event`, `calendar.update_event`, `calendar.delete_event` | NO | **YES** (Policy Gated) | NO | NO |
| **Knowledge RAG** | `knowledge.search_hybrid`, `knowledge.read_chunk`, `knowledge.expand` | NO | NO | **YES** | NO |
| **Drive Read** | `drive.search`, `drive.read`, `drive.download` | NO | NO | **YES** | NO |
| **Drive Mutate** | `drive.upload`, `drive.rename`, `drive.move`, `drive.delete` | NO | NO | NO (V1 Read-only Research) | NO |
| **Web Search** | `web.search` | NO | NO | **YES** | NO |
| **Orchestration** | `supervisor.plan`, `supervisor.replan` | NO | NO | NO | **YES** |

---

## 3. Sandboxing & Read-Only Contexts
- **Research Sandbox**: When `KnowledgeResearchAgent` executes a research task, `CapabilityGate` constructs a strictly read-only `ToolRegistry` view. Even if the underlying service has write methods, they are completely excluded from the agent's function definitions.
- **Untrusted Content Isolation**:
  - Retrieved documents, incoming emails, and web pages are treated as untrusted data strings.
  - They are passed into LLM prompts in distinct XML/fenced containers (e.g. `<untrusted_content>...</untrusted_content>`).
  - Untrusted data **MUST NOT** be allowed to override system instructions or elevate capability access.

---

## 4. Capability Enforcement Pipeline
```text
Agent Execution Request
          |
          v
   CapabilityGate.get_tools_for_agent(agent_role, execution_context)
          |
   [Filters tools according to Agent Role & Read-Only constraints]
          |
          v
   Bound Tool Definitions -> LLM Model API Call
          |
   LLM Tool Call Request
          |
          v
   PolicyEngine.evaluate(tool_name, tool_args, context)
          |
     +----+----+
     |         |
   AUTO     APPROVAL -> ApprovalManager (HITL Pause/Resume)
     |
     v
   Tool Execution
```
