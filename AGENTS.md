# Agent guide

Conventions for anyone — human or AI agent — changing this repository. `README.md` explains what the assistant does and how to run it; this file says how to work on it.

## Code style

- **Comments: general, not line by line.** Give each module, class and public function a short docstring that explains the idea — what it is for and the approach. Do not narrate the code with inline comments. An inline comment is fine only for something the code cannot say: a workaround for an external system, a security constraint, a magic threshold — one line, and say *why*.
- **Small files, grouped folders.** Keep a file under ~650 lines; split by responsibility (mixins, sub-modules) and keep old import paths working through re-exports. When a folder grows past ~12 files, group them into sub-packages (`retrieval/search|evidence|answer`, `ingestion/parsing|profiling|enrichment|…`).
- **Match the surrounding code**: naming, typing (full annotations; pyright must pass), error handling (catch the narrowest exception; a broad `except Exception` needs `# noqa: BLE001 - <reason>`).
- **User-facing text is Vietnamese**; code, identifiers, logs and docstrings are English.
- **No secrets in source.** Credentials come from `.env` / environment variables (see `scripts/create_admin.py`). Never commit `.env`, `.secrets/`, `data/`, `storage/`, `eval_results/` or model files.

## Before every push: run CI locally

Push only when every step of `.github/workflows/ci.yml` passes on your machine:

```bash
uv run ruff check app tests scripts
uv run ruff format --check app tests scripts
uv run pyright
uv run pytest --tb=short --cov --cov-report=term-missing      # coverage gate in pyproject.toml

# security
uv export --frozen --no-dev --no-hashes --no-emit-project > requirements-audit.txt
uvx pip-audit -r requirements-audit.txt --no-deps --disable-pip --ignore-vuln PYSEC-2026-3804
uvx bandit -r app scripts -q --severity-level medium --confidence-level medium
docker run --rm -v "$PWD:/repo:ro" zricethezav/gitleaks:v8.28.0 detect --source /repo --config /repo/.gitleaks.toml --redact
docker run --rm -v "$PWD:/repo:ro" aquasec/trivy:0.65.0 config --severity HIGH,CRITICAL --exit-code 1 /repo

# frontend (when it changed)
cd frontend && npm ci && npx tsc -p tsconfig.app.json --noEmit && npx oxlint src && npm run build && npm audit --audit-level=high
```

The SQLite migration step of CI is the inline script in `ci.yml`. CodeQL (`.github/workflows/codeql.yml`) and Dependabot run on GitHub only. A new vulnerability with no fixed release may be ignored in `ci.yml` only with a comment saying why it does not apply and when to remove the ignore.

## Security rules

- Every write to Google (calendar, mail, Drive) goes through an approval; never add a path that writes without one.
- Retrieved document text is untrusted: it reaches the model inside the injection boundary (`retrieval/answer/injection_boundary.py`); images reach it only as descriptions.
- Hugging Face models are pinned to a revision (`EMBEDDING__REVISION`, `RERANKER__REVISION`); update the pin deliberately.
- Containers run as non-root users; keep it that way when editing the Dockerfiles.
- Images are served only to users who can read a document that refers to them (`GET /documents/images/{key}`).

## Working on the stack

- Postgres, Redis, backend and frontend run in Docker inside WSL. The backend mounts `./app`; after a Python change run `docker restart assistant_backend`. After rebuilding images run `docker builder prune -af` (the WSL disk lives on drive C).
- LLM calls go through an OpenAI-compatible gateway (`ROUTER_BASE_URL`); from Windows scripts use `ROUTER_BASE_URL=http://localhost:20128/v1`.
- Ingest inside the backend image (GPU), with the backend stopped: `scripts/ingestion/ingest_corpus.py --root data/<folder or file> [--force]`. Backfills under `scripts/backfill/` refresh stored data without re-parsing.
- Back up the database before a migration (`pg_dump` from the `assistant_postgres` container).

## Layout

See the "Cấu trúc thư mục" section of `README.md`. Tests mirror `app/` under `tests/unit/`; integration tests skip themselves when Postgres is unavailable.
