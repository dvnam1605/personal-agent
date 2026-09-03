# P10C Review Pack: Policies & Safety

> Spec scope: P10-14..P10-20. Authorized under P10 split; implemented + self-reviewed + verified green.
> This pack is generated against the exact working tree (v3 post-review fixes applied).

## 1. Summary

P10C completes the policy & safety boundary between retrieval output and answer synthesis:

```text
EvidenceBundle (P10B)
  -> SufficiencyChecker           deterministic rules: empty/min-items/compare-missing/score-threshold (P10-16)
  -> apply_retry_strategy         bounded retry (max 2 attempts) with strategy modifications          (P10-17)
  -> enforce_compare_diversity    compare-document coverage audit & balance report                     (P10-14)
  -> sanitize_evidence_for_prompt structural <retrieved_document> boundary wrapping                    (P10-20)
  -> AnswerSynthesizer.synthesize pluggable LLM synthesis + citation extraction & mapping              (P10-18/19)
```

## 2. Deliverables

New files:

```text
app/domain/models/sufficiency.py           # SufficiencyStatus & SufficiencyVerdict (P10-16)
app/domain/models/citation.py              # Citation model (P10-19)
app/services/retrieval/compare_policy.py   # CompareResult & enforce_compare_diversity (P10-14)
app/services/retrieval/sufficiency.py      # SufficiencyChecker deterministic rules (P10-16)
app/services/retrieval/retry.py            # RetryPolicy, RetryStrategy & apply_retry_strategy (P10-17)
app/services/retrieval/injection_boundary.py # BOUNDARY_INSTRUCTIONS & sanitize_evidence_for_prompt (P10-20)
app/services/retrieval/prompts.py          # SYNTHESIS_SYSTEM_PROMPT & build_synthesis_user_message (P10-18/20)
app/services/retrieval/synthesis.py        # AnswerSynthesizer protocol + PromptAnswerSynthesizer + citation extractor (P10-18/19)
tests/unit/services/test_retrieval_policies.py # 67 unit and security tests
```

Modified:

```text
app/domain/models/retrieval.py             # Evidence provenance fields (evidence_id, score, rerank_score, title, filename, source_type, document_version_id)
app/services/retrieval/sql.py              # ANCHOR_SELECT_COLUMNS, PARENT_FETCH_TEMPLATE, SIBLING_FETCH_TEMPLATE select doc & page metadata
app/services/retrieval/packing.py          # unit_for_chunk populates score, rerank_score, and citation provenance
app/services/retrieval/expansion.py        # _parent_units & _neighbor_units populate score and proper parent row anchors
app/services/retrieval/pipeline.py         # Added run_with_sufficiency() and run_with_synthesis() methods (preserving PARTIAL status & unique attempt trace_ids)
app/services/retrieval/__init__.py         # Extended public API exports
docs/architecture/rag-retrieval-strategy.md # Stamped §8–§11 as implemented (P10C ✅)
```

## 3. Spec Traceability

| Task | Decision implemented | Where |
|---|---|---|
| P10-14 | Compare-doc audit: verifies every requested document receives evidence representation; outputs `CompareResult` with `covered_documents`, `missing_documents`, and `balance_ratio`. | `compare_policy.py` |
| P10-15 | Stable `evidence_id: str` (uuid4 hex), `score`, `rerank_score`, `title`, `filename`, `source_type`, `document_version_id` preserved on `Evidence` items. | `retrieval.py`, `packing.py`, `expansion.py` |
| P10-16 | Deterministic sufficiency: zero items → INSUFFICIENT, below min items → INSUFFICIENT, compare missing doc → PARTIAL, all scores < threshold → INSUFFICIENT (`min_score_threshold=0.0` default for V1 RRF scale, reading `item.rerank_score`/`item.score` directly). | `sufficiency.py` |
| P10-17 | Bounded retry: hard-capped at max 2 attempts. Generates modified queries via `INCREASE_K`, `RELAX_FILTER`, `CHANGE_EXPANSION`. Distinct trace_id per attempt (`{trace_id}-att{attempt}`). | `retry.py`, `pipeline.py` |
| P10-18 | `AnswerSynthesizer` protocol + `PromptAnswerSynthesizer` default. Internal-only evidence enforcement (`internal_only=False` raises `NotImplementedError`), explicit insufficient-evidence handling, PARTIAL status preservation, missing-document warning injection. | `synthesis.py`, `prompts.py`, `pipeline.py` |
| P10-19 | `Citation` model maps extracted `[evidence_id]` markers back to document metadata (title, page anchors, heading path, source type). Safe fallback for hallucinated markers. | `citation.py`, `synthesis.py` |
| P10-20 | Structural prompt injection boundary: XML-style `<retrieved_document>` tags with `html.escape` attributes + `<retrieved_document` and `</retrieved_document>` tag escaping in content + system-level declarations that documents cannot alter system rules, tool permissions, CapabilityGate, or PolicyEngine. Security tests for 5 attack vectors. | `injection_boundary.py`, `prompts.py`, `test_retrieval_policies.py` |

## 4. Verification

```text
uv run --frozen --no-sync -m pytest tests/unit/services/test_retrieval_policies.py -v   # 67 passed in 0.37s
uv run --frozen --no-sync -m pytest tests/unit/services/test_retrieval_pipeline.py -v   # 32 passed in 0.20s
uv run --frozen --no-sync -m pytest tests/unit -q                                       # Full suite passed (exit 0)
uv run --frozen --no-sync ruff check app tests alembic                                  # All checks passed!
uv run --frozen --no-sync pyright app/services/retrieval app/domain/models/retrieval.py app/domain/models/sufficiency.py app/domain/models/citation.py tests/unit/services/test_retrieval_policies.py # 0 errors, 0 warnings
```

## 5. Review Findings Resolution (v3 Post-Review Fixes)

| Finding | Severity | Description | Resolution |
|---|---|---|---|
| **H1 / H-NEW** | HIGH | Score scale mismatch in Sufficiency Rule 5 (RRF scores are $\le 0.033 < 0.15$) | Set `DEFAULT_MIN_SCORE_THRESHOLD = 0.0` for V1 baseline with IdentityReranker/RRF scale. Added short-circuit `if self._min_score <= 0.0: return False` to disable score filtering until calibrated cross-encoder scores land in P10D. Documented in `rag-retrieval-strategy.md` §9. Added unit tests for both default threshold with RRF scores and explicit calibrated thresholds (0.15). |
| **H2** | HIGH | Evidence contract empty (`title`/`filename`/`source_type`/`document_version_id` were None) | Updated `ANCHOR_SELECT_COLUMNS`, `PARENT_FETCH_TEMPLATE`, and `SIBLING_FETCH_TEMPLATE` to select `d.title`, `d.source_type`, `d.uri`, `d.version_number`. Propagated all fields into `Evidence` items in `unit_for_chunk`, `_parent_units`, and `_neighbor_units`. Updated `build_citations_from_bundle` to populate citation provenance. |
| **H3 / L-NEW 1** | HIGH | Injection boundary breakout via `</retrieved_document>`, `<retrieved_document`, and attribute `"` | In `sanitize_evidence_for_prompt`, escaped both `</retrieved_document>` and `<retrieved_document` in `content_raw` and applied `html.escape(..., quote=True)` to all attribute values (`id`, `document_id`, `title`, `section`). Added breakout security tests. |
| **H4** | HIGH | `run_with_synthesis` swallowed `PARTIAL` status and lost missing documents | `run_with_synthesis` now preserves `status=verdict.status` (returning `PARTIAL` when compare/document search is incomplete) and passes `missing_documents` to `build_synthesis_user_message` to inject `## Coverage Warning` into the prompt. |
| **M1** | MEDIUM | PARENT page anchors used child hit instead of parent row | `PARENT_FETCH_TEMPLATE` now selects `c.page_start`, `c.page_end`, `c.citation_label`. `_parent_units` reads `chunk_anchor_metadata(row)` for parent units. Added unit test. |
| **M2** | MEDIUM | `CHANGE_EXPANSION` retry strategy was restricted to `NONE` only | Updated `CHANGE_EXPANSION` in `retry.py` so any `query.expansion_policy != ExpansionPolicy.PARENT` transitions to `PARENT`. Added unit test. |
| **M3** | MEDIUM | Retry reused the same `retrieval_trace_id` for both attempts | `run_with_sufficiency` in `pipeline.py` assigns `{root_trace_id}-att{attempt}` per attempt. Added unit test. |
| **M4** | MEDIUM | Compare audit post-packing only | `SufficiencyChecker` Rule 3 audits missing compare documents and marks `PARTIAL` to trigger retry; `compare_result` is computed with `current_query` on the final bundle. |
| **M-NEW** | MEDIUM | `internal_only` parameter silently ignored in synthesis | `PromptAnswerSynthesizer.synthesize` now checks `if not internal_only:` and raises `NotImplementedError("V1 synthesis only supports internal_only=True; external knowledge is not configured.")` plus logs warning. Added unit test. |
| **L1** | LOW | `Evidence.evidence_id` random uuid per packing | Recorded for P10D; citations within each synthesized response resolve deterministically against that specific bundle. |
| **L2** | LOW | Citation regex `[hex32]` safety against hallucinated markers | Added test `test_extract_cited_ids_hallucinated` asserting non-hex / hallucinated tags are safely ignored. |
| **L3** | LOW | Missing documents not in synthesis prompt | Injected `## Coverage Warning` into `build_synthesis_user_message` when `missing_documents` is non-empty. Added test. |
| **L-NEW 2** | LOW | `_stub_generate` could return placeholder silently | Added structured warning log in `_stub_generate` to alert operator when no real LLM callback is injected. |

## 6. Gate Status

```text
GATE STATUS: WAITING FOR USER REVIEW — P10C (v3 post-review fixes verified green, all findings resolved)
Next gate message expected: APPROVED P10C   (then P10D benchmark, ablations & family review)
```

---
*Review Pack updated 2026-08-28 (v3 post-review fixes)*
