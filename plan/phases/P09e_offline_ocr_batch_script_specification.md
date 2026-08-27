> Active phase file for P9E. Read `../MASTER_PLAN.md` and the P9 overview first.
> Do not implement until the user sends `APPROVED P9E`.
>
> P9E is INDEPENDENT of P9B/P9C/P9D ordering. It may be implemented any time
> after P9A; its output feeds the pipeline through
> `source_type="preparsed_markdown"`.

# P9E — OFFLINE OCR BATCH SCRIPT — EXECUTION SPECIFICATION

## Objective

A standalone script converts a FOLDER of scanned PDFs into a FOLDER of Markdown
files. It runs OUTSIDE the assistant runtime, on a stronger GPU machine, as a
one-shot offline batch — never in the request path.

## Entry criteria

```text
[ ] strong-GPU machine available with engine deps installed in a separate
    environment from the assistant runtime:
    paddleocr + paddlepaddle (paddle engines) or surya-ocr (surya_2)
[ ] P9A APPROVED (preparsed_markdown source_type + fingerprint contract exist)
```

---

## P9E-1 — Script contract (was P9-07A)

```text
scripts/ocr_batch.py

INPUT : --input-dir  ./scanned_pdfs        (*.pdf, recursive optional)
OUTPUT: --output-dir ./parsed_md           (mirrored tree, *.md + sidecar)
MODEL : --engine paddleocr_vl_1_6 | paddle_ppstructurev3 | surya_2
DEVICE: --device cuda:0 | cpu              (page-by-page by default)
```

Requirements:

```text
[ ] one failed PDF must not abort the batch (per-file error report)
[ ] page-by-page processing to bound VRAM usage
[ ] output Markdown preserves headings hierarchy and table structure
[ ] sidecar JSON per file: source checksum, engine+version, ocr_used,
    pages processed, warnings, duration
[ ] deterministic naming: <stem>.md next to <stem>.pdf (or mirrored tree)
[ ] idempotent re-runs: skip outputs whose sidecar checksum matches input
[ ] no network calls; model weights loaded from local cache
```

The resulting Markdown folder is ingested through
`source_type="preparsed_markdown"`; fingerprinting (P9A-3) applies unchanged,
using the sidecar checksum. The assistant runtime itself never needs the OCR
model installed.

## P9E-2 — Dependency isolation

`paddleocr`/`paddlepaddle` and `surya-ocr` MUST NOT enter the assistant
runtime's required dependencies. Options (pick one, record in Review Pack):

```text
a) optional dependency groups [project.optional-dependencies]
   ocr-paddle = [...] and ocr-surya = [...]
b) requirements-ocr.txt / requirements-ocr-surya.txt for the external
   machine only
```

The script file lives in the repo but imports paddle/surya lazily/locally so
CI and the runtime suite never touch them.

## P9E-3 — Verification constraints

Full engine execution happens on the external machine; CI verifies everything
around it:

```text
[ ] unit tests: argument parsing, naming/mirroring logic, sidecar schema,
    idempotent skip decision, per-file failure isolation (engine mocked)
[ ] dry-run mode executes without paddle/surya installed (--dry-run walks
    inputs, writes planned outputs/sidecars without OCR)
[ ] a committed pre-parsed Markdown sample fixture matches sidecar schema
    consumed by ingestion (P9B fixture reuse)
[ ] runtime suite stays green with paddle/surya absent
```

---

## Definition of Done

```text
[ ] scripts/ocr_batch.py implemented per contract
[ ] dependency isolation chosen and recorded
[ ] unit tests + dry-run green in main environment (no paddle/surya needed)
[ ] sample sidecar + Markdown fixture committed
[ ] usage documented (README section or docs snippet) for the external machine
[ ] full suite green (ruff + pyright + pytest)
[ ] Review Pack section generated
[ ] user APPROVED (closes P9 family)
```
