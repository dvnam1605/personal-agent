# Personal AI Assistant

A capability-bounded, multi-agent AI assistant designed for productivity and scale.

## Architecture
- 3 Execution paths: Direct Specialist, Known Workflow Graphs, Open-ended Supervisor DAG.
- 4 Core Specialist Agent roles: `SupervisorAgent`, `CommunicationAgent`, `CalendarAgent`, `KnowledgeResearchAgent`.
- Strict capability gating, memory gating, and human-in-the-loop (HITL) policy verification.

## Development Setup
```bash
uv venv
uv pip install -e ".[dev]"
```

## Running Tests
```bash
pytest
```

## Offline OCR Batch (P9E, external GPU machine only)
Converts a folder of scanned PDFs into Markdown for ingestion via
`source_type="preparsed_markdown"`. OCR engines never enter the runtime:
install them from optional groups on the strong-GPU machine, then run one-shot:

```bash
pip install -e ".[ocr-paddle]"   # or ".[ocr-surya]"
python scripts/ocr_batch.py --input-dir ./scanned_pdfs --output-dir ./parsed_md \
    --engine paddleocr_vl_1_6 --device cuda:0 [--recursive] [--force] [--dry-run]
```

Each output gets `<stem>.md` plus a `<stem>.ocr.json` sidecar (source checksum,
engine+version, ocr_used, pages, warnings, duration); re-runs skip files whose
sidecar checksum still matches. Point the assistant's ingestion at the output
folder with `source_type="preparsed_markdown"`.
