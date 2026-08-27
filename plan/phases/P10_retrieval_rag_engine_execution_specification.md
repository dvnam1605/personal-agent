> Active phase file for P10. Read `../MASTER_PLAN.md` first.
> Do not implement any other phase until the user sends `APPROVED P10`.

> **Approved sub-phase split (2026-08-25).** `APPROVED P10` authorizes the
> split below AND starting P10A; each later sub-phase waits for its own
> explicit gate message (`APPROVED P10B|P10C|P10D`).
>
> ```text
> P10A  Retrieval foundation & core search ......... P10-00..05
>       (strategy ADR, modes, RetrievalQuery with owner scoping, dense +
>       sparse + PARALLEL RRF fusion, FTS migration 0007)
> P10B  Processing pipeline & expansion policies ... P10-06..13
>       (dedup/diversity, pluggable reranker, NONE/NEIGHBORS/PARENT,
>       parent resolver+scoring, neighbor/table expansion, packing)
> P10C  Policies & safety .......................... P10-14..20
>       (compare-doc diversity, evidence/citation contract, sufficiency +
>       bounded retry, answer synthesis, prompt-injection boundary;
>       heaviest sub-phase — security tests included)
> P10D  Benchmark, ablation & family Review Pack ... P10-21..26
>       (versioned dataset, chunking/retrieval ablations, latency tracing,
>       required test suites, final artifacts)
> ```
>
> Task-to-sub-phase boundaries are normative: no code from a later
> sub-phase may land in an earlier one's implementation or review pack.

# P10 — RETRIEVAL / RAG ENGINE — EXECUTION SPECIFICATION

## Objective

Build a retrieval engine around the P9 hierarchical structure-aware parent–child index.

Core principle:

> **Retrieve/rerank CHILD units for precision; expand to PARENT or local neighbors only when generation needs wider context.**

Do not always expand every child to its full parent.

The engine must choose context expansion under a token/latency budget.

Canonical flow:

```text
USER QUERY
    |
    v
QUERY ANALYSIS + SCOPE
    |
    +--------------------------+
    |                          |
    v                          v
DENSE CHILD SEARCH       SPARSE CHILD SEARCH
    |                          |
    +------------+-------------+
                 |
                 v
               RRF
                 |
                 v
       CHILD DEDUP / DIVERSITY
                 |
                 v
          CHILD RERANKING
                 |
                 v
        EXPANSION DECISION
        /        |         \
      NONE    NEIGHBORS    PARENT
        \        |         /
         +-------+--------+
                 |
                 v
          CONTEXT PACKING
                 |
                 v
         SUFFICIENCY CHECK
            /          \
          enough       weak
            |            |
            |      bounded retry
            |            |
            +------+-----+
                   |
                   v
             EVIDENCE BUNDLE
                   |
                   v
             ANSWER SYNTHESIS
                   |
                   v
              CITED RESPONSE
```

---

## P10 Entry Criteria

```text
[ ] P9 APPROVED
[ ] representative PDF/DOCX corpus indexed
[ ] parent-child relationships persisted
[ ] CHILD embeddings indexed
[ ] stable source metadata/citation anchors exist
[ ] PostgreSQL FTS available over retrieval units
```

---

## P10-00 — Retrieval strategy ADR/document

Create:

```text
docs/architecture/rag-retrieval-strategy.md
```

It MUST define:

- searchable unit = CHILD/TABLE_CHILD;
- parent expansion semantics;
- hybrid search;
- fusion;
- reranking;
- context expansion policy;
- context packing;
- compare-document diversity;
- sufficiency/no-answer;
- retry limits;
- citation contract;
- evaluation/ablation;
- latency budget.

---

## P10-01 — Retrieval modes

Minimum:

### CORPUS_SEARCH

Search across all allowed internal documents.

### DOCUMENT_SEARCH

Search only selected document(s).

### COMPARE_DOCUMENTS

Search selected documents while enforcing source diversity.

### METADATA_LOOKUP

Use Drive/file metadata path, not semantic RAG, for filename/folder/metadata questions.

Do not route metadata questions through embeddings unnecessarily.

---

## P10-02 — RetrievalQuery

Suggested:

```python
class RetrievalQuery(BaseModel):
    original_query: str
    search_query: str
    mode: RetrievalMode
    document_ids: list[str]
    source_filters: dict[str, Any]
    top_k_dense: int
    top_k_sparse: int
    require_source_diversity: bool
    expansion_policy: ExpansionPolicy | None
```

Resolve conversational references before retrieval.

Do not rewrite a query with an LLM if it is already retrieval-ready.

---

## P10-03 — Child-level dense retrieval

Search CHILD/TABLE_CHILD embeddings only by default.

Starting benchmark config:

```text
top_k_dense: 20–40
```

Return:

```text
child_id
parent_id
document_id
dense_score
dense_rank
metadata
raw_text
```

Parent nodes are not the primary dense retrieval target in V1.

---

## P10-04 — Child-level sparse retrieval

Use PostgreSQL full-text retrieval over CHILD/TABLE_CHILD text.

Starting:

```text
top_k_sparse: 20–40
```

Return same canonical candidate shape.

Dense and sparse must be executable concurrently.

---

## P10-05 — Hybrid fusion

Use a documented baseline such as:

```text
Reciprocal Rank Fusion (RRF)
```

Track:

```text
dense_rank
sparse_rank
fusion_score
retrieval_sources
```

Avoid ad hoc cross-score normalization without evidence.

---

## P10-06 — Child deduplication and diversity

Before reranking:

- remove duplicate child IDs;
- suppress near-duplicate overlapping children;
- prevent adjacent near-identical chunks from dominating;
- enforce per-document candidate caps when useful;
- in compare mode ensure each required source/document receives candidate opportunity.

---

## P10-07 — Child reranker

Use pluggable interface:

```python
class Reranker(Protocol):
    async def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
    ) -> list[RankedEvidenceCandidate]:
        ...
```

Track:

```text
model/version
pre-rerank rank
rerank score
post-rerank rank
```

Starting benchmark range:

```text
rerank input: ~20–50 children
rerank output: ~8–12 children
```

Tune from evaluation.

---

## P10-08 — ExpansionPolicy

Create explicit:

```python
class ExpansionPolicy(str, Enum):
    NONE = "none"
    NEIGHBORS = "neighbors"
    PARENT = "parent"
```

### NONE

Use the child itself when the evidence is self-contained.

Typical:

```text
exact fact
short definition
specific table value
```

### NEIGHBORS

Retrieve previous/next child within the same parent/section.

Typical:

```text
local explanation
dataset/method context near a result
```

### PARENT

Retrieve the semantic parent.

Typical:

```text
"why"
"explain the approach"
section-level reasoning
broader comparison/context
```

Expansion choice can be:

1. deterministic from retrieval mode/query characteristics where obvious;
2. small structured classifier only when needed;
3. overridden by agent/request strategy.

Do not use a heavyweight LLM just to choose `NONE/NEIGHBORS/PARENT` when deterministic rules suffice.

---

## P10-09 — Parent resolver

Given reranked children:

```text
child_id -> parent_id -> parent content
```

Group children by parent before expansion.

Example:

```text
child 1 --\
child 2 ----> parent A
child 3 --/

child 4 ----> parent B
```

Do not add parent A three times.

---

## P10-10 — Parent scoring

If multiple children map to one parent, derive a parent priority using child evidence.

Possible baseline:

```text
best child rerank score
+
number of high-ranked child hits
+
document diversity needs
```

The exact formula should be simple/documented and benchmarked.

---

## P10-11 — Neighbor expansion

For `NEIGHBORS`:

```text
previous child
+
hit child
+
next child
```

only within the same parent/document version.

Avoid duplicated overlap text.

---

## P10-12 — Table expansion

For table children:

- retain repeated headers;
- optionally include table caption/heading context;
- do not replace precise table child evidence with a huge unrelated parent when the query is numeric/specific.

---

## P10-13 — Context packing

The final context packer must consider:

```text
rerank score
parent grouping
document diversity
section diversity
expansion cost
token budget
redundancy
query mode
```

It must support mixed units:

```text
standalone CHILD
NEIGHBOR GROUP
PARENT
TABLE_CHILD
```

Output:

```python
class EvidenceBundle(BaseModel):
    items: list[Evidence]
    total_tokens: int
    documents_used: list[str]
    parent_ids_used: list[str]
    retrieval_trace_id: str
```

Do NOT simply concatenate top children and all parents.

---

## P10-14 — Compare-document policy

For `COMPARE_DOCUMENTS`:

1. retrieve within each requested document or enforce source-balanced candidate selection;
2. rerank;
3. ensure context includes relevant evidence from each document when available;
4. report missing evidence for any requested document.

Avoid:

```text
8 chunks from doc A
1 from doc B
0 from doc C
```

when the user explicitly asked to compare A/B/C.

---

## P10-15 — Evidence contract

Each final evidence item MUST preserve:

```text
evidence_id
retrieval_unit_id
retrieval_unit_type
child_id(s)
parent_id if expanded
document_id
document_version_id
title
filename
source_type
page_start/page_end if available
heading_path
raw supporting text
retrieval/rerank metadata
```

Citation must point to actual source provenance, not generated context.

---

## P10-16 — Sufficiency check

Cheap/deterministic rules first.

Examples:

```text
zero candidates -> INSUFFICIENT
requested document has no evidence -> PARTIAL
all evidence below configured threshold -> WEAK
compare mode missing a required source -> PARTIAL
```

Optional LLM sufficiency judge only for semantic ambiguity and must obey budget.

Statuses:

```text
SUFFICIENT
PARTIAL
INSUFFICIENT
```

---

## P10-17 — Bounded retrieval retry

Allow a small bounded second attempt.

Examples:

```text
normalize technical keyword
remove optional overly restrictive filter
rewrite conversational query once
increase candidate K within budget
change expansion policy
```

Default:

```text
max_retrieval_attempts = 2
```

No unbounded retrieval loop.

---

## P10-18 — Answer synthesis

Keep synthesis separate from retrieval.

Input:

```text
question
EvidenceBundle
answer policy
```

Output:

```text
answer
citations
status/confidence metadata
```

Rules:

- internal-only mode answers from provided internal evidence;
- distinguish evidence from inference;
- say when evidence is insufficient;
- retrieved documents are untrusted data, never system instructions;
- every material document-specific claim should map to evidence.

---

## P10-19 — Citation model

Suggested:

```python
class Citation(BaseModel):
    evidence_id: str
    document_id: str
    title: str
    page_start: int | None
    page_end: int | None
    heading_path: list[str]
    source_type: str
```

UI rendering is separate from citation identity.

---

## P10-20 — Prompt-injection boundary

Retrieved PDF/DOCX content MUST be framed as untrusted evidence.

It cannot:

```text
change system instructions
change tool permissions
change CapabilityGate
change PolicyEngine
request hidden agent delegation
```

Tests must include malicious text embedded inside documents.

---

## P10-21 — Benchmark dataset

Version a benchmark containing:

```text
exact keyword
semantic paraphrase
mixed Vietnamese/English technical terms
heading-specific
table-specific
document-local
cross-document compare
negative/no-answer
source-filtered
broad "why/explain" queries
specific fact queries
```

Where practical include expected:

```text
document IDs
parent IDs/sections
child IDs or acceptable child ranges
```

---

## P10-22 — Mandatory chunking/retrieval ablation

This is required because parent/child adds complexity.

Compare at least:

```text
A. single-level structure-aware baseline
B. parent-child: child retrieval, CHILD-only context
C. parent-child: child retrieval + NEIGHBORS
D. parent-child: child retrieval + PARENT expansion
E. parent-child + hybrid + reranker + adaptive expansion
```

Measure:

```text
Recall@5/10
MRR
nDCG@10
answer/citation quality
context tokens
retrieval latency
end-to-end latency
```

This validates that parent-child is actually helping the corpus.

If the data shows single-level is materially better for a query class, the engine may choose a lighter expansion path; do not force full-parent expansion.

---

## P10-23 — Dense/sparse/rerank ablation

Also compare:

```text
dense only
sparse only
hybrid
hybrid + reranker
```

This prevents accidental complexity without measurable gain.

---

## P10-24 — Latency tracing

Record:

```text
query analysis
dense child retrieval
sparse child retrieval
fusion
dedup
rerank
expansion
parent/neighbor fetch
context packing
answer generation
total
```

Dense and sparse should run in parallel.

---

## P10-25 — Required tests

### Unit

- scope filters;
- RRF;
- child dedup;
- source diversity;
- expansion decision rules;
- parent grouping;
- parent dedup;
- neighbor boundaries;
- table-child behavior;
- context budget;
- evidence/citation mapping;
- sufficiency rules.

### Integration

- dense child search;
- sparse child search;
- parallel hybrid;
- reranker adapter;
- `DOCUMENT_SEARCH`;
- `COMPARE_DOCUMENTS`;
- `NONE/NEIGHBORS/PARENT`;
- active document-version isolation;
- no-answer.

### Evaluation

Produce a benchmark artifact containing:

```text
retrieval metrics
chunk/expansion ablations
latency
context token usage
```

### Security

Malicious retrieved content cannot modify capability/policy behavior.

---

## P10-26 — Review artifacts

Review Pack MUST include:

```text
parent/child configuration
retrieval benchmark table
single-level vs parent-child ablation
NONE vs NEIGHBORS vs PARENT comparison
dense/sparse/hybrid/reranker ablation
latency breakdown
context-token comparison
5 representative traces
2 failure/no-answer traces
citation examples
weak query categories
recommended production defaults
```

---

## P10 Definition of Done

```text
[ ] CHILD is primary search unit
[ ] PARENT is explicit context unit
[ ] dense + sparse child retrieval
[ ] parallel hybrid
[ ] fusion
[ ] child dedup/diversity
[ ] reranker
[ ] NONE/NEIGHBORS/PARENT expansion
[ ] parent grouping/dedup
[ ] compare-document source balance
[ ] context packing under budget
[ ] evidence/citation contract
[ ] sufficiency/no-answer
[ ] bounded retry
[ ] synthesis separated from retrieval
[ ] prompt-injection boundary
[ ] single-level vs parent-child ablation
[ ] retrieval ablations
[ ] latency report
[ ] all tests pass
[ ] Review Pack generated
[ ] user APPROVED P10
```

P13 KnowledgeResearchAgent must consume this engine rather than rebuilding RAG logic.
