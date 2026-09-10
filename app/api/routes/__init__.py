"""API routes registry."""

from fastapi import APIRouter

from app.api.routes.approvals import router as approvals_router
from app.api.routes.google_auth import router as google_auth_router
from app.api.routes.health import router as health_router
from app.api.routes.questions import router as questions_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(google_auth_router)
api_router.include_router(approvals_router)
api_router.include_router(questions_router)

__all__ = [
    "api_router",
    "approvals_router",
    "google_auth_router",
    "health_router",
    "questions_router",
]
