"""Unit tests for TracingManager and LangSmith span failure isolation."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.infrastructure.observability.tracing import TracingManager


@pytest.mark.asyncio
async def test_tracing_manager_disabled_noop() -> None:
    """Verify disabled tracing executes user block with zero network overhead."""
    mgr = TracingManager(tracing_enabled=False)

    async with mgr.trace_span("test_disabled_span", span_type="tool") as span:
        assert span.name == "test_disabled_span"
        assert span.span_type == "tool"

    assert span.span_id is not None


@pytest.mark.asyncio
async def test_tracing_manager_enabled_span_lifecycle() -> None:
    """Verify enabled tracing calculates duration and sets completed status."""
    mgr = TracingManager(tracing_enabled=True)

    async with mgr.trace_span("agent_triage", span_type="agent", run_id="run_100") as span:
        await asyncio.sleep(0.01)
        span.metadata["route"] = "direct_specialist"

    assert span.status == "completed"
    assert span.run_id == "run_100"
    assert span.duration_ms > 0.0
    assert span.metadata["route"] == "direct_specialist"


@pytest.mark.asyncio
async def test_tracing_manager_error_capture() -> None:
    """Verify exceptions inside span are recorded on span while propagating to caller."""
    mgr = TracingManager(tracing_enabled=True)

    with pytest.raises(ValueError, match="Simulated tool error"):
        async with mgr.trace_span("failing_tool", span_type="tool") as span:
            raise ValueError("Simulated tool error")

    assert span.status == "error"
    assert span.error == "Simulated tool error"
    assert span.duration_ms >= 0.0


@pytest.mark.asyncio
async def test_tracing_emits_langsmith_run_when_api_key_configured() -> None:
    """L1: enabled tracing with an API key flushes through langsmith.Client."""
    mock_client = MagicMock()
    with (
        patch("app.core.config.settings.langsmith.api_key", "lsv2_test_key"),
        patch("langsmith.Client", return_value=mock_client) as client_cls,
    ):
        mgr = TracingManager(tracing_enabled=True)
        async with mgr.trace_span("llm_call", span_type="llm", run_id="run_ls"):
            pass

    client_cls.assert_called_once()
    mock_client.create_run.assert_called_once()
    kwargs = mock_client.create_run.call_args.kwargs
    assert kwargs["name"] == "llm_call"
    assert kwargs["run_type"] == "llm"
    assert kwargs["extra"] == {"assistant_run_id": "run_ls"}
