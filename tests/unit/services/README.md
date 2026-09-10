# Service unit tests

Grouped by purpose so the folder is not a flat dump of ~50 files.

| Folder | What it covers |
|---|---|
| `ingestion/` | Parse, chunk, embed, orchestrate, OCR, fingerprint |
| `retrieval/` | Hybrid search, policies, benchmark, ViRanker |
| `context/` | Memory, entity, compaction |
| `approvals/` | Policy engine, question plane, HMAC tokens |
| `google/` | Calendar, Drive, OAuth |
| `routing/` | FastTriage, workflow registry, capability gate |
| `skills/` | Skill registry, definitions, execution |
| `platform/` | Persistence, budget, retention, spill, workers |

`pytest tests/unit/services` still collects every file. Point at a subfolder to run one cluster, e.g. `pytest tests/unit/services/routing`.
