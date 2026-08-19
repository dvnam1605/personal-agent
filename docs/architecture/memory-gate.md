# Memory Gate Architecture Contract

## 1. Problem Statement
Naively injecting full conversation history, user preferences, and entity graphs into every LLM call causes:
- Excessive latency (waiting on vector search over memory stores).
- Token waste and context pollution.
- Attention degradation on simple, unambiguous user requests.

---

## 2. MemoryGate Pipeline

```text
User Request
     |
     v
MemoryGate Evaluation
  /               \
SKIP               RETRIEVE
 |                   |
 |              Retrieve relevant
 |              facts, preferences & entities
 |                   |
 +---------+---------+
           |
           v
     Fast Triage / Execution
```

### Retrieval Conditions (WHEN TO RETRIEVE):
Memory retrieval is triggered if and only if:
1. **Pronoun / Anaphora Resolution**: References like *"that document"*, *"the meeting with him"*, *"the previous draft"*.
2. **Personalized Preference Reliance**: Requests requiring user preferences (e.g. *"Schedule lunch at my usual spot"*).
3. **Explicit Cross-Session Continuity**: Inquiries about previous conversations or decisions.
4. **Entity Disambiguation**: Resolving nicknames or recurring colleagues.

### Skip Conditions (WHEN TO SKIP):
Memory retrieval is skipped for:
1. Explicit, self-contained single-domain queries (e.g. *"What is my calendar for tomorrow?"*).
2. Explicit queries with exact search arguments (e.g. *"Search emails with subject 'Invoice 123'"*).
3. Standard informational queries that do not depend on user state.

---

## 3. Memory Consolidation Rule (Off Critical Path)

```text
User Request
     |
     v
Return User Response (Immediate)
     |
     v
Background Task (Celery / Async Task)
     |
     +--> Extract Candidate Memories / Entity Updates
     +--> Validate & De-duplicate
     +--> Persist to PostgreSQL Long-Term Memory
```

**Hard Invariant**:
> The assistant **MUST NEVER** block the user's interactive response path in order to perform optional long-term memory extraction or consolidation.
