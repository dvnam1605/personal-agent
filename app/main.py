"""Application entrypoint and FastAPI factory."""

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.api.routes.google_auth import router as google_auth_router
from app.api.routes.health import router as health_router
from app.core.config import Environment, settings
from app.core.logging import get_logger, setup_logging
from app.domain.errors import AppError
from app.harness.checkpointer import (
    close_checkpointer_lifespan,
    configure_checkpointer_lifespan,
)
from app.infrastructure.db.session import get_session_factory
from app.services.platform.audit import AuditOutboxWorker
from app.services.platform.retention import RetentionWorker


async def _configure_consumed_token_store(logger: structlog.stdlib.BoundLogger) -> None:
    """Wire Redis SET NX for single-use approval tokens (H6). TESTING stays in-memory."""
    from app.infrastructure.redis.client import redis_manager
    from app.services.approvals.consumed_store import (
        RedisConsumedTokenStore,
        configure_default_store,
    )

    if settings.environment is Environment.TESTING:
        return
    healthy = await redis_manager.health_check()
    if healthy:
        client = await redis_manager.get_client()
        configure_default_store(RedisConsumedTokenStore(client))
        logger.info("consumed_token_store.redis")
        return
    if settings.environment not in (Environment.DEVELOPMENT, Environment.TESTING):
        raise RuntimeError(
            "Redis is required for the single-use approval token store in "
            f"{settings.environment.value}. In-memory consume is not cross-worker safe."
        )
    logger.warning("consumed_token_store.in_memory_fallback")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifespan events."""
    setup_logging()
    logger = get_logger("app.lifespan")
    logger.info(
        "application.startup",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment.value,
    )
    stop_event = asyncio.Event()
    session_factory = get_session_factory()
    outbox_worker = AuditOutboxWorker(session_factory)
    retention_worker = RetentionWorker(session_factory)
    worker_tasks = [
        asyncio.create_task(outbox_worker.run(stop_event), name="audit-outbox-worker"),
        asyncio.create_task(retention_worker.run(stop_event), name="retention-worker"),
    ]
    if settings.auth_enforced and not settings.security.api_key:
        logger.error(
            "security.api_key_missing",
            detail=(
                "No SECURITY__API_KEY is configured while authentication is enforced; "
                "authenticated endpoints will reject all requests."
            ),
        )
    await _configure_consumed_token_store(logger)
    checkpointer_cm = await configure_checkpointer_lifespan(logger)

    # Warmup LocalEmbeddingService in background (pre-loads weights into RAM without blocking startup)
    if settings.environment is not Environment.TESTING:

        async def _warmup_embedding() -> None:
            try:
                from app.services.retrieval.factory import get_shared_embedding_service

                logger.info("embedding.model_warmup_start")
                embedding_svc = get_shared_embedding_service()
                await embedding_svc.embed_query("khởi động mô hình kiểm tra")
                logger.info("embedding.model_warmup_complete")
            except Exception as exc:  # noqa: BLE001 - warmup error should not block boot
                logger.warning("embedding.model_warmup_failed", error=str(exc))

        asyncio.create_task(_warmup_embedding(), name="embedding-warmup")

    try:
        yield
    finally:
        stop_event.set()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        await close_checkpointer_lifespan(checkpointer_cm, logger)
        try:
            from app.harness.runtime import get_default_context_runtime

            await get_default_context_runtime().consolidation_worker.drain()
        except Exception as exc:  # noqa: BLE001 - shutdown drain must not block exit
            logger.debug("consolidation_worker_drain_failed", error=str(exc))
        logger.info("application.shutdown")


def create_app() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url=f"{settings.api_prefix}/docs" if settings.docs_enabled else None,
        redoc_url=f"{settings.api_prefix}/redoc" if settings.docs_enabled else None,
        openapi_url=f"{settings.api_prefix}/openapi.json" if settings.docs_enabled else None,
    )

    # CORS configuration — explicit origin allowlist; credentials stay disabled
    # because authentication is header-based, never cookie-based (M10).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-User-ID",
            "X-Request-ID",
        ],
    )

    # Contextual Request ID and Latency Middleware
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )

        start_time = time.perf_counter()
        logger = get_logger("app.http")
        logger.info("http.request.received")

        try:
            response: Response = await call_next(request)
            latency_ms = (time.perf_counter() - start_time) * 1000
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time-Ms"] = f"{latency_ms:.2f}"
            logger.info(
                "http.request.completed",
                status_code=response.status_code,
                latency_ms=round(latency_ms, 2),
            )
            return response
        except Exception as exc:  # noqa: BLE001 - log then re-raise unhandled request errors
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "http.request.failed",
                error=str(exc),
                latency_ms=round(latency_ms, 2),
                exc_info=True,
            )
            raise

    # Global Domain Error Exception Handler
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger = get_logger("app.error")
        logger.warning(
            "domain.error",
            error_code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            details=exc.details,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    # Generic Unhandled Exception Handler
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger = get_logger("app.error")
        logger.error("unhandled.error", error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected internal server error occurred.",
                    "details": {},
                }
            },
        )

    # Route mounting
    app.include_router(health_router, prefix="")  # Root /health and /ready
    app.include_router(
        google_auth_router, prefix=""
    )  # OAuth callback matches local Google client JSON
    from app.api.routes.approvals import router as approvals_router
    from app.api.routes.query import router as query_router
    from app.api.routes.questions import router as questions_router

    app.include_router(approvals_router, prefix="")
    app.include_router(questions_router, prefix="")
    app.include_router(query_router, prefix="")
    app.include_router(api_router, prefix=settings.api_prefix)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
