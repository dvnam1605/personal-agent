"""Structured logging with contextual tracing and sensitive data filtering."""

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

from app.core.config import settings

# Sensitive keys to redact from logs
SENSITIVE_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "secret",
    "client_secret",
    "authorization",
    "cookie",
}


def _is_sensitive(key: str) -> bool:
    return any(sensitive in key.lower() for sensitive in SENSITIVE_KEYS)


def _censor_value(key: str, value: Any) -> Any:
    """Recursively redact sensitive keys inside dicts, lists, and tuples."""
    if _is_sensitive(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: _censor_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_censor_value(key, item) for item in value]
    return value


def censor_sensitive_data(
    logger: WrappedLogger | logging.Logger | None, method_name: str, event_dict: EventDict
) -> EventDict:
    """Censor potential tokens, passwords, and sensitive keys from log output."""
    for key, value in list(event_dict.items()):
        event_dict[key] = _censor_value(key, value)
    return event_dict


def setup_logging() -> None:
    """Configure structured logging for the application."""
    log_level = logging.DEBUG if settings.debug else logging.INFO

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        censor_sensitive_data,
    ]

    if settings.environment == "development":
        # Pretty console logging in development
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # JSON formatting for production / staging
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Obtain a structured bound logger instance."""
    return structlog.get_logger(name)
