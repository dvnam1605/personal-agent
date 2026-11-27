# syntax=docker/dockerfile:1
# Multi-stage backend image: the builder resolves the locked environment with uv,
# the runner gets only the virtualenv and sources and runs as a non-root user.
# Python matches the version CI tests (3.14); uv is pinned to the version that
# writes uv.lock, so `--frozen` installs exactly what CI installs.

FROM python:3.14-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/
COPY skills/ ./skills/
COPY docker-entrypoint.sh ./
# hatchling reads [project].readme during install
COPY README.md ./

RUN uv sync --frozen --no-dev --no-editable
RUN chmod +x docker-entrypoint.sh

FROM python:3.14-slim-bookworm AS runner

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
