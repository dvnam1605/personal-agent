"""API routes registry.

Every router is mounted once, at the root path: the frontend and nginx call
`/query`, `/documents`, … directly. Only the OpenAPI docs live under
`settings.api_prefix`. `ROUTERS` keeps the mount order; the query router comes
last because it owns the broadest paths.
"""

from fastapi import APIRouter

from app.api.routes.approvals import router as approvals_router
from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.documents import router as documents_router
from app.api.routes.google_auth import router as google_auth_router
from app.api.routes.health import router as health_router
from app.api.routes.memories import router as memories_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.query import router as query_router
from app.api.routes.questions import router as questions_router
from app.api.routes.runs import router as runs_router
from app.api.routes.tasks import router as tasks_router
from app.api.routes.telegram import router as telegram_router

ROUTERS: tuple[APIRouter, ...] = (
    health_router,
    google_auth_router,
    auth_router,
    conversations_router,
    runs_router,
    documents_router,
    approvals_router,
    questions_router,
    notifications_router,
    memories_router,
    telegram_router,
    tasks_router,
    query_router,
)

__all__ = [
    "ROUTERS",
    "approvals_router",
    "auth_router",
    "conversations_router",
    "documents_router",
    "google_auth_router",
    "health_router",
    "memories_router",
    "notifications_router",
    "query_router",
    "questions_router",
    "runs_router",
    "tasks_router",
    "telegram_router",
]
