"""Unit tests for TracingManager and LangSmith span failure isolation."""

import asyncio

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
