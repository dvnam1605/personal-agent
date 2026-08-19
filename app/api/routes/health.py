"""Health and readiness probe endpoints."""

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str = "ready"
    checks: dict[str, Any]


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
    status_code=status.HTTP_200_OK,
    summary="Readiness Probe",
)
async def readiness_check() -> ReadinessResponse:
    """Return application readiness status checking dependencies."""
    # In P1, mocked/baseline checks return healthy status
    checks = {
        "database": {"status": "ok", "mocked": True},
        "redis": {"status": "ok", "mocked": True},
        "configuration": {"status": "ok"},
    }
    return ReadinessResponse(
        status="ready",
        checks=checks,
    )
