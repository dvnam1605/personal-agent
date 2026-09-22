"""API routes registry."""

from fastapi import APIRouter

from app.api.routes.approvals import router as approvals_router
from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.google_auth import router as google_auth_router
from app.api.routes.health import router as health_router
from app.api.routes.query import router as query_router
from app.api.routes.questions import router as questions_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(conversations_router)
api_router.include_router(google_auth_router)
api_router.include_router(approvals_router)
api_router.include_router(questions_router)
api_router.include_router(query_router)

__all__ = [
    "api_router",
    "approvals_router",
    "auth_router",
    "conversations_router",
    "google_auth_router",
    "health_router",
    "query_router",
    "questions_router",
]
