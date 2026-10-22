# syntax=docker/dockerfile:1
# Multi-stage production build for FastAPI Backend using Astral uv

FROM python:3.12-slim-bookworm AS builder

# Install uv from official Astral image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Copy dependency specifications
COPY pyproject.toml uv.lock ./

# Install dependencies into virtual environment without the root project
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source code and migrations
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY skills/ ./skills/
COPY docker-entrypoint.sh ./
# hatchling reads [project].readme during install
COPY README.md ./

# Final sync including the project (non-editable: no bind-mount of sources)
RUN uv sync --frozen --no-dev --no-editable
RUN chmod +x docker-entrypoint.sh

# --- Production Runner Stage ---
FROM python:3.12-slim-bookworm AS runner

WORKDIR /app

# curl: container healthcheck. libxcb1/libgl1/libglib2.0-0: OpenCV (pulled in
# by docling's PDF layout pipeline) cannot load without them, so every PDF
# ingest inside the container failed with "libxcb.so.1: cannot open".
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libxcb1 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv and application artifacts from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Run as an unprivileged user (uid 1000 matches the default WSL user, so the
# bind-mounted storage/ and models/ stay writable).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/.secrets /app/storage \
    && chown -R app:app /app/.secrets /app/storage
USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
