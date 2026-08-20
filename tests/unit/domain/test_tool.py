"""Unit tests for Tool contracts and cross-field security/outcome invariants."""

import pytest
from pydantic import ValidationError

from app.domain.enums import ActionRiskLevel
from app.domain.models import (
    ToolContext,
    ToolDefinition,
    ToolExecutionMetadata,
    ToolInput,
    ToolResult,
)


def test_tool_definition_valid() -> None:
    """Verify tool contract declaration with consistent risk and mutation flag."""
    tool_def = ToolDefinition(
        name="gmail.send_message",
        description="Send an email message via Gmail API.",
        parameters_schema={
            "type": "object",
            "properties": {
                "recipient": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["recipient", "subject", "body"],
        },
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        is_mutation=True,
        required_scopes=["https://www.googleapis.com/auth/gmail.send"],
    )

    assert tool_def.name == "gmail.send_message"
    assert tool_def.is_mutation is True
    assert tool_def.risk_level == ActionRiskLevel.HIGH_IMPACT_WRITE
    assert len(tool_def.required_scopes) == 1


def test_tool_definition_contradictory_mutation_risk_rejected() -> None:
    """Verify mutation tools cannot be marked READ_ONLY and vice versa."""
    with pytest.raises(ValidationError, match="is_mutation=True but has risk_level=READ_ONLY"):
        ToolDefinition(
            name="gmail.send_message",
            description="Send email",
            risk_level=ActionRiskLevel.READ_ONLY,
            is_mutation=True,
        )

    with pytest.raises(ValidationError, match="mutation risk level"):
        ToolDefinition(
            name="gmail.delete_message",
            description="Delete email",
            risk_level=ActionRiskLevel.IRREVERSIBLE,
            is_mutation=False,
        )


def test_tool_input_and_context() -> None:
    """Verify tool input arguments and execution context."""
    inp = ToolInput(
        tool_name="gmail.send_message",
        arguments={"recipient": "user@example.com", "subject": "Hi", "body": "Hello"},
    )
    ctx = ToolContext(
        run_id="run_123",
        user_id="user_456",
        agent_name="CommunicationAgent",
        read_only_view=False,
    )

    assert inp.tool_name == "gmail.send_message"
    assert ctx.run_id == "run_123"
    assert ctx.read_only_view is False


def test_tool_result_success_and_failure_valid() -> None:
    """Verify successful and failed ToolResult representations."""
    meta_ok = ToolExecutionMetadata(
        tool_name="gmail.list_messages",
        latency_ms=145.2,
        cached=False,
        retry_count=0,
    )
    res_ok = ToolResult(
        tool_name="gmail.list_messages",
        success=True,
        output={"messages": [{"id": "m1"}]},
        metadata=meta_ok,
    )
    assert res_ok.success is True
    assert res_ok.output == {"messages": [{"id": "m1"}]}
    assert res_ok.error is None

    meta_err = ToolExecutionMetadata(
        tool_name="gmail.send_message",
        latency_ms=50.0,
        retry_count=1,
    )
    res_err = ToolResult(
        tool_name="gmail.send_message",
        success=False,
        error="Permission denied: missing write scope",
        metadata=meta_err,
    )
    assert res_err.success is False
    assert res_err.output is None
    assert "Permission denied" in (res_err.error or "")


def test_tool_result_mismatched_audit_name_rejected() -> None:
    """Verify ToolResult rejects mismatched tool_name vs metadata.tool_name."""
    meta = ToolExecutionMetadata(
        tool_name="gmail.list_messages",
        latency_ms=10.0,
    )
    with pytest.raises(ValidationError, match="must match metadata tool_name"):
        ToolResult(
            tool_name="calendar.delete_event",
            success=True,
            output={"status": "ok"},
            metadata=meta,
        )


def test_tool_result_outcome_consistency_rejected() -> None:
    """Verify ToolResult rejects conflicting outcome combinations (success with error, failure with output, failure without error)."""
    meta = ToolExecutionMetadata(
        tool_name="gmail.list_messages",
        latency_ms=10.0,
    )

    # success=True with error
    with pytest.raises(ValidationError, match="has success=True but contains error"):
        ToolResult(
            tool_name="gmail.list_messages",
            success=True,
            output={"data": 1},
            error="Something went wrong",
            metadata=meta,
        )

    # success=False with output
    with pytest.raises(ValidationError, match="has success=False but contains output"):
        ToolResult(
            tool_name="gmail.list_messages",
            success=False,
            output={"data": 1},
            error="Failed",
            metadata=meta,
        )

    # success=False without error
    with pytest.raises(ValidationError, match="must contain an error message"):
        ToolResult(
            tool_name="gmail.list_messages",
            success=False,
            error=None,
            metadata=meta,
        )


def test_tool_execution_metadata_naive_timestamp_rejected() -> None:
    """Verify ToolExecutionMetadata rejects timezone-naive timestamps."""
    import datetime as dt

    naive_dt = dt.datetime(2026, 1, 1, 12, 0, 0)
    with pytest.raises(ValidationError, match="must be UTC-aware"):
        ToolExecutionMetadata(
            tool_name="test_tool",
            latency_ms=5.0,
            timestamp=naive_dt,
        )


def test_tool_restriction_validation() -> None:
    """Verify ToolRestriction validation and pattern normalization."""
    from app.domain.models import ToolRestriction

    res = ToolRestriction(allow=["gmail.*", "calendar.read"], deny=["gmail.send"])
    assert res.allow == ["gmail.*", "calendar.read"]
    assert res.deny == ["gmail.send"]

    # Either allow or deny must be present
    with pytest.raises(ValidationError, match="must specify at least 'allow' or 'deny'"):
        ToolRestriction()

    # Empty pattern list rejected
    with pytest.raises(ValidationError, match="cannot be empty"):
        ToolRestriction(allow=[])

    # Blank pattern entries rejected
    with pytest.raises(ValidationError, match="cannot be blank"):
        ToolRestriction(allow=[" "])

