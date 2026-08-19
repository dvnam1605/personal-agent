"""API routes registry."""

from fastapi import APIRouter

from app.api.routes.google_auth import router as google_auth_router
from app.api.routes.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(google_auth_router)
