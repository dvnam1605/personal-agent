"""Shared HTTP client settings for calls to the LLM gateway."""

from __future__ import annotations

import httpx

# A local gateway that is down refuses or hangs on connect; failing fast here
# keeps one unreachable dependency from holding a request for minutes.
LLM_CONNECT_TIMEOUT_SECONDS = 10.0


def llm_http_timeout(read_seconds: float) -> httpx.Timeout:
    """Long read timeout for slow generations, short connect timeout."""
    return httpx.Timeout(read_seconds, connect=min(LLM_CONNECT_TIMEOUT_SECONDS, read_seconds))
