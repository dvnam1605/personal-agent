"""LangSmith observability and distributed tracing manager with failure isolation."""

import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


@dataclass
class TraceSpan:
    """Represents a single trace span in the execution graph."""

    span_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    span_type: str = "custom"
    run_id: str | None = None
    parent_span_id: str | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    duration_ms: float = 0.0
    status: str = "running"
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class TracingManager:
    """Manages distributed tracing and LangSmith integration with guaranteed failure isolation."""

    def __init__(self, tracing_enabled: bool | None = None) -> None:
        self.tracing_enabled = (
            tracing_enabled if tracing_enabled is not None else settings.langsmith.tracing_enabled
        )

    @asynccontextmanager
    async def trace_span(
        self,
        name: str,
        span_type: str = "custom",
        run_id: str | None = None,
        parent_span_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncGenerator[TraceSpan, None]:
        """Context manager yielding a TraceSpan with error capture and zero-network no-op if disabled."""
        span = TraceSpan(
            name=name,
            span_type=span_type,
            run_id=run_id,
            parent_span_id=parent_span_id,
            metadata=metadata or {},
        )

        if not self.tracing_enabled:
            # Completely no-op path without external network calls
            try:
                yield span
            finally:
                pass
            return

        try:
            logger.debug(
                "span_started",
                span_name=name,
                span_type=span_type,
                run_id=run_id,
                span_id=span.span_id,
            )
            yield span
            span.status = "completed"
        except Exception as exc:
            span.status = "error"
            span.error = str(exc)
            logger.warning(
                "span_error_captured",
                span_name=name,
                span_id=span.span_id,
                error=str(exc),
            )
            raise
        finally:
            span.end_time = time.monotonic()
            span.duration_ms = (span.end_time - span.start_time) * 1000.0
            try:
                logger.debug(
                    "span_finished",
                    span_name=name,
                    span_id=span.span_id,
                    duration_ms=span.duration_ms,
                    status=span.status,
                )
            except Exception as log_err:
                logger.warning("tracing_flush_failed", error=str(log_err))


tracing_manager = TracingManager()
