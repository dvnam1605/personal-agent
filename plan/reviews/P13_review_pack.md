# Phase P13 Review Pack: KnowledgeResearchAgent

> Phase: **P13 — KNOWLEDGERESEARCHAGENT**
> Status: **IMPLEMENTED, TESTED, AND VERIFIED GREEN**
> Scope: **per `phases/P13_knowledgeresearchagent.md`**
> Gate in: **APPROVED P12** received from user 2026-09-07 ("approved p12").

---

## 1. Executive Summary

Phase 13 delivers the third production specialist: evidence-based research
over internal RAG (P10 pipeline), Drive files (P08 tools), and external web
sources (new provider seam). Like P12, it is a thin deterministic domain layer
on the unchanged P11 runtime — plus two new read-only tool suites, since
`retrieval.*` / `web.search` did not exist yet.

```text
internal_task ──► DIRECT ──► retrieval.synthesize(internal_only=true) ──► cited answer / INSUFFICIENT
web_task      ──► ReAct  ──► web.search ──► URL-labeled claims
mixed_task    ──► ReAct  ──► synthesize + web.search ──► [Tài liệu nội bộ] / [Nguồn mở rộng / Web]
zero hits     ──► explicit Vietnamese no-answer (never a guess)
injection doc ──► stays inside <retrieved_document> (escaped, quoted)
any mutation  ──► POLICY stop, NEEDS_APPROVAL, executor never called
```

---

## 2. Deliverables by Spec Item

| Spec | Artifact | Notes |
|---|---|---|
| P13 §4.1 visibility (declaration) | `app/agents/declarations.py` (+`__init__` exports) | `KnowledgeResearchAgent` (`KNOWLEDGE_RESEARCH`, categories `["retrieval","drive","web"]`, caps `retrieval.*` + drive read labels + `web.search`); `BOUNDED_REACT` default. `drive.list_folder` shares the drive.read label and is visible too — read-only, documented as accepted. Drive mutations share NO label with these patterns: they are invisible even in the full view, doubly stripped by read-only |
| P13 §4.1 tools (NEW — did not exist) | `app/tools/knowledge.py`, `app/tools/__init__.py` | `retrieval.retrieve` (evidence + sufficiency, boundary-wrapped), `retrieval.synthesize` (cited answer + status), `web.search` (URL-labeled); `RetrievalTools(pipeline)` / `WebSearchTools(provider)` wrappers mirroring the Google-tool execute/invoke/failure contract; all `is_mutation=False` |
| Web seam (entry criterion) | `WebSearchProvider` Protocol + `MockWebSearchProvider` | Scripted query→page map, truncation, query log; no network. Real provider is a later-phase swap |
| Web models | `app/domain/models/web.py` (+`__init__` exports) | `WebSearchResultItem` (mandatory URL provenance), `WebSearchPage`; frozen/validated |
| P13 §3 modes | `app/agents/specialist/knowledge_research.py` | `ResearchMode` enum; `internal_task` (DIRECT), `web_task`/`mixed_task` (ReAct, budget ≤4 steps / ≤4000 prompt tokens per §5.2); `complex_task` parity builder; preamble embeds `BOUNDARY_INSTRUCTIONS` verbatim |
| P13 §3 Mode 3 sections | `INTERNAL_SECTION_HEADER` / `EXTERNAL_SECTION_HEADER` + `mixed_report()` | Canonical `[Tài liệu nội bộ]` / `[Nguồn mở rộng / Web]` headers (P10 prompts had no fixed headers — defined here) |
| P13 §1 no-answer policy | `insufficient_report()` | SUCCESS + `data.sufficiency=INSUFFICIENT` + pipeline-identical Vietnamese message |
| P13 §5.1 unit | `tests/unit/agents/test_knowledge_research_agent.py` (14), `test_agent_declarations.py` (+2), `tests/unit/domain/test_web.py` (3) | All 6 named spec tests + edge/alias/registry tests + KR declaration/gating tests + web-model contract tests |

No LLM calls, no network, no secrets in any new code or test
(`FixedHybrid`/`FakeProvider`/fake `generate`/`MockWebSearchProvider`/
`ScriptedChat`/`DictExecutor` only).

---

## 3. Spec-vs-Code Mappings (decided, not deferred)

1. `for_agent("knowledge_research", read_only=True)` (§4.1): the declaration
   name is `KnowledgeResearchAgent` (consistent with P12 + MASTER_PLAN);
   `"knowledge_research"` is its `Domain` value. Stronger than convention:
   drive mutation tools share no capability label with the declaration, so
   they are absent even from the full view (asserted), and the read-only view
   strips the remainder. A runner-level test additionally proves a mis-scoped
   view carrying a mutation is still stopped at POLICY.
2. `LatencyBudgetManager` (§3.2): same P11 mapping as P12 — `ExecutionBudget`
   (`max_react_steps=4`, `max_prompt_tokens=4000` per §5.2) + runner stops.
3. `SpecialistStatus` has no INSUFFICIENT/PARTIAL: pipeline `SufficiencyStatus`
   travels in tool output + report `data`; the agent report itself is SUCCESS
   (task completed with an explicit no-answer) — never BLOCKED for this case.
4. Benchmark/latency scenarios (§5.2: 50-question legal benchmark, ≤8s
   end-to-end): the corpus/benchmark harness is P10D scope; P13 asserts the
   token side (≤4000 prompt tokens) and per-component determinism. A live
   50-question benchmark run needs a populated corpus + LLM `generate`
   callback and belongs to P20 evaluation.

---

## 4. Verification

- New tests: **19 passed** (14 knowledge-research + 2 declaration + 3 web-model;
  spec's 6 named tests all present).
- Full suite: **737 passed, 0 failed, 0 errors, 0 skipped** (718 P12 + 19 new;
  includes 10 live-PG tests).
- Coverage on new/touched modules: `tools/knowledge.py` **100%**,
  `specialist/knowledge_research.py` **100%**, `domain/models/web.py` **100%**,
  `agents/declarations.py` **100%**.
- `ruff check app tests`: **pass**. `ruff format --check` on all touched
  files: **clean**.
- `pyright` on new/touched app files: **0 errors** (one finding fixed by
  passing `search_query` explicitly per repo convention).
- Pass criteria §6: (1) 3 modes function ✓; (2) citation accuracy — every
  cited ID in test answers resolves to a bundle evidence ID ✓; (3) zero
  mutation exposure — read-only view audit + runner POLICY test ✓;
  (4) injection immunity — escaped boundary + no-derailment tests ✓;
  (5) suite green ✓.

---

## 5. Out of Scope / Deferred (unchanged)

- Real `WebSearchProvider` implementation (key/transport) — mock seam only.
- Runtime pipeline/session wiring for tools (`for_user` constructors) — P16+.
- `ApprovalService` live execution, chat API, Supervisor (P15+).
- 50-question live benchmark + ≤8s latency validation (P20 evaluation).

---

## 6. Gate Request

P13 is complete per §6. **STOP — awaiting `APPROVED P13` before any P14 work.**
Next spec on approval: `phases/P14_skill_system_first_dynamic_skills.md`.
