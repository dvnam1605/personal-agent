"""LangSmith observability and distributed tracing manager with failure isolation."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

import structlog

from app.core.config import settings
from app.core.sanitization import sanitize_payload, sanitize_string

logger = structlog.get_logger(__name__)

_LANGSMITH_RUN_TYPES = frozenset(
    {"llm", "chain", "tool", "retriever", "embedding", "prompt", "parser"}
)
_TRACE_METADATA_ALLOWLIST = frozenset(
    {
        "route",
        "tool_name",
        "status",
        "span_type",
        "agent_name",
        "agent",
        "run_id",
        "task_id",
        "workflow_id",
        "error_type",
    }
)


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


def _emit_langsmith_run(span: TraceSpan) -> None:
    """Best-effort SDK flush. Never raises into the traced operation (L1)."""
    if not settings.langsmith.api_key:
        return
    try:
        from langsmith import Client

        client = Client(
            api_url=settings.langsmith.endpoint or None,
            api_key=settings.langsmith.api_key or None,
        )
        raw_run_type = span.span_type if span.span_type in _LANGSMITH_RUN_TYPES else "chain"
        run_type = cast(
            Literal["tool", "chain", "llm", "retriever", "embedding", "prompt", "parser"],
            raw_run_type,
        )
        redacted_inputs = sanitize_payload(
            {
                key: value
                for key, value in span.metadata.items()
                if key in _TRACE_METADATA_ALLOWLIST
            },
            max_string_len=200,
            allowed_keys=set(_TRACE_METADATA_ALLOWLIST),
        )
        redacted_error = sanitize_string(span.error, max_string_len=200) if span.error else None
        client.create_run(
            name=span.name or "span",
            run_type=run_type,
            inputs=redacted_inputs if isinstance(redacted_inputs, dict) else {},
            project_name=settings.langsmith.project,
            run_id=span.span_id,
            parent_run_id=span.parent_span_id,
            extra={"assistant_run_id": span.run_id},
            error=redacted_error,
            outputs={"status": span.status, "duration_ms": span.duration_ms},
            start_time=datetime.now(UTC),
        )
    except Exception as exc:  # noqa: BLE001 - tracing must never fail the request
        logger.warning("langsmith_emit_failed", error=str(exc))


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
        except Exception as exc:  # noqa: BLE001 - capture then re-raise
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
            except Exception as log_err:  # noqa: BLE001 - logging isolation
                logger.warning("tracing_flush_failed", error=str(log_err))
            _emit_langsmith_run(span)


tracing_manager = TracingManager()
