"""Health and readiness probe endpoints with real dependency checks."""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import settings
from app.infrastructure.db.session import get_session_factory
from app.infrastructure.redis.client import redis_manager

router = APIRouter(tags=["Health"])

PROBE_TIMEOUT_SECONDS = 2.0


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, Any]


async def check_database() -> bool:
    """Verify the application database answers a trivial query."""
    try:
        session_factory = get_session_factory()

        async def _probe() -> None:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))

        await asyncio.wait_for(_probe(), timeout=PROBE_TIMEOUT_SECONDS)
        return True
    except Exception:
        return False


async def check_redis() -> bool:
    """Verify Redis responds to PING."""
    try:
        return await asyncio.wait_for(redis_manager.health_check(), timeout=PROBE_TIMEOUT_SECONDS)
    except Exception:
        return False


def check_configuration() -> bool:
    """Report whether mandatory runtime configuration is present and valid."""
    try:
        if not settings.database.url or not settings.database.url.strip():
            return False
        if settings.embedding.dimensions <= 0 or not settings.embedding.model:
            return False
        if settings.auth_enforced and not (
            settings.security.api_key and settings.security.api_key.strip()
        ):
            return False

        # Validate Google credentials only when explicitly configured
        secrets_file = settings.google.client_secrets_file
        if secrets_file and Path(secrets_file).is_file():
            try:
                import json

                content = json.loads(Path(secrets_file).read_text(encoding="utf-8"))
                if not isinstance(content, dict) or not (
                    "web" in content or "installed" in content
                ):
                    return False
            except Exception:
                return False

        if settings.redis.url and not (
            settings.redis.url.startswith("redis://") or settings.redis.url.startswith("rediss://")
        ):
            return False

        return True
    except Exception:
        return False


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Liveness Probe",
)
async def health_check() -> HealthResponse:
    """Return application liveness status."""
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment.value,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness Probe",
)
async def readiness_check() -> ReadinessResponse | JSONResponse:
    """Return readiness based on live database and Redis dependency checks."""
    db_ok, redis_ok = await asyncio.gather(check_database(), check_redis())
    config_ok = check_configuration()
    checks = {
        "database": {"status": "ok" if db_ok else "error"},
        "redis": {"status": "ok" if redis_ok else "error"},
        "configuration": {"status": "ok" if config_ok else "error"},
    }
    if db_ok and redis_ok and config_ok:
        return ReadinessResponse(status="ready", checks=checks)
    return JSONResponse(status_code=503, content={"status": "degraded", "checks": checks})


__all__ = [
    "check_configuration",
    "check_database",
    "check_redis",
    "router",
]
