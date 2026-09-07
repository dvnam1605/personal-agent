> Active phase specification for P19. Read `../MASTER_PLAN.md` first.
> Do not implement any downstream phase (P20) until the user sends `APPROVED P19`.

# P19 — WORKFLOW HARDENING + MEETING PREP GRAPH SPECIFICATION

## 1. Objective & Architectural Scope

Phase 19 demonstrates the core architectural lifecycle of the assistant: **Skill -> Hardened Static Graph (§5.3)**:
1. In P14, `MeetingPrepSkill` was introduced as a flexible, prompt-guided dynamic skill.
2. In P19, the empirical execution traces of Meeting Prep are analyzed.
3. Once the execution topology is proven stable and beneficial, the workflow is compiled into **`WF-05: MeetingPrepGraph`**, a deterministic, high-performance `StateGraph` registered in `StaticWorkflowRegistry`.
4. Hardening `WF-05` into a compiled graph eliminates Supervisor replanning overhead, enforces parallel fan-out via `Send`, and provides guaranteed latency and cost bounds for the assistant's most critical workflow.

### Resolution of Single-Ownership Contract (Fixing "KEEP AS SKILL" Ambiguity)
- In earlier drafts, an ambiguous clause permitted keeping Meeting Prep indefinitely as an uncompiled skill, leaving the ownership and evaluation of WF-05 in Phase 20 undefined.
- **Definitive Contract**: In P19, **`WF-05: MeetingPrepGraph` is compiled, tested, and promoted as the primary production execution path**. The original prompt-guided skill is archived as an authoring prototype. In Phase 20, WF-05 is evaluated strictly as a compiled `StateGraph`.

---

## 2. Entry Criteria

Before P19 implementation begins, all of the following MUST be satisfied:
- [x] **APPROVED P18**: Policy Engine, HITL approval plane, and write permissions are fully operational.
- [x] **Supervisor & Memory Operational**: Dynamic Supervisor DAG (P16) and Memory/Entity store (P17) are operational.
- [x] **Historical Trace Corpus**: At least 30 recorded execution traces of meeting prep runs available from test runs or benchmarks.
- [x] **Static Workflow Registry Active**: `StaticWorkflowRegistry` from P15 ready to accept new graph registrations.

---

## 3. The Hardened Meeting Prep Graph (`WF-05`)

### 3.1. Canonical Graph Topology

```text
               [User Query: "Chuẩn bị họp ngày mai với Nam"]
                                    │
                                    ▼
                         [Node 1: IdentifyMeeting]
                     (CalendarAgent: list_events/search)
                                    │
                                    ▼
                         [Node 2: ResolveContext]
             (Extract attendees, company, agenda, past dates)
                                    │
                  +-----------------+-----------------+
                  │ (Send API)                        │ (Send API)
                  ▼                                   ▼
        [Node 3A: EmailResearch]            [Node 3B: DocumentResearch]
      (CommunicationAgent: search)        (KnowledgeResearchAgent: RAG/Drive)
      - Find recent email threads         - Retrieve relevant specs/contracts
      - Extract commitments/action items  - Identify key policy references
                  │                                   │
                  +-----------------+-----------------+
                                    │ (Fan-in Reducer)
                                    ▼
                          [Node 4: SynthesizeDossier]
                       (Structured Executive Briefing)
                                    │
                                    ▼
                                  [END]
```

### 3.2. Structural Graph Invariants
- **Parallelism (§13)**: Node 3A (`EmailResearch`) and Node 3B (`DocumentResearch`) MUST run concurrently via LangGraph's `Send` API.
- **Least-Privilege Isolation**:
  - Node 3A is restricted to read-only communication tools.
  - Node 3B is restricted to read-only RAG/Drive tools.
  - No mutation tools are exposed anywhere in `WF-05`.
- **Dossier Schema (`MeetingDossier`)**:
  - `meeting_id`: String
  - `event_summary`: String
  - `scheduled_time`: Aware Datetime
  - `attendees`: List of resolved contact profiles
  - `recent_discussions`: Bulleted summary of recent email threads
  - `relevant_documents`: List of cited internal documents with `[evidence_id]`
  - `suggested_talking_points`: Actionable agenda items

---

## 4. Benchmark & Comparative Validation (P19-04)

To prove graph superiority over dynamic ReAct / skill execution, a benchmark comparison is conducted across 20 synthetic meeting scenarios:

| Metric | Dynamic Skill Baseline | Hardened WF-05 Graph | Target Threshold |
|---|---|---|---|
| Average Latency | ~12.5s | <= 7.0s | **>= 30% reduction** |
| Total LLM Calls | 6 - 9 calls | 2 - 3 calls | **>= 50% reduction** |
| Parallel Efficiency | 0% (sequential) | 100% (concurrent 3A/3B) | **Verified concurrent execution** |
| Hallucination / Deviation | Occasional loop/mis-route | 0% (deterministic DAG) | **0% topology deviation** |
| Citation Completeness | Variable | 100% cited claims | **100% verified citations** |

---

## 5. Test Matrix & Scenarios

### 5.1. Unit Tests (`tests/unit/workflows/test_meeting_prep_graph.py`)
- `test_meeting_prep_graph_compilation`: Verify graph compiles into valid executable `CompiledGraph`.
- `test_identify_meeting_node_locates_target_event`: Verify calendar lookup by date and title keywords.
- `test_resolve_context_extracts_attendees`: Verify attendee list and topic extraction.
- `test_parallel_email_and_doc_research`: Verify concurrent execution of Nodes 3A and 3B.
- `test_fan_in_reducer_merges_research_evidence`: Verify evidence bundle combines email and RAG units cleanly.
- `test_synthesize_dossier_generates_schema`: Verify final output adheres to `MeetingDossier` Pydantic model.
- `test_missing_meeting_graceful_exit`: Verify clean message when no calendar event matches query.

### 5.2. Integration & Stress Tests
- `test_meeting_prep_full_e2e_with_mocks`: Full end-to-end execution from prompt to structured dossier.
- `test_meeting_prep_timeout_and_budget_guards`: Verify execution halts cleanly within configured latency budget (15s).

---

## 6. Pass Criteria & Exit Gate

The phase is complete and ready for `APPROVED P19` review when:
1. **Compilation & Registration**: `WF-05` is registered in `StaticWorkflowRegistry` and invocable via `RouteDecision(target_workflow_id="WF-05")`.
2. **Benchmark Proof**: Benchmark proves >= 30% latency reduction and >= 50% LLM call reduction compared to dynamic skill baseline.
3. **Deterministic Structure**: 100% of test runs adhere to the 4-stage DAG without unexpected loop iterations.
4. **Unit Test Coverage**: >= 90% line coverage on `app/workflows/meeting_prep.py`.
5. **No Regressions**: Full test suite passes green (`pytest`, `ruff`, `pyright`).
