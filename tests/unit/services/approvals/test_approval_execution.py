"""Unit tests for routing and user-facing messages of approved tool execution."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.models import ToolContext, ToolExecutionMetadata, ToolInput, ToolResult
from app.infrastructure.db.models import ApprovalRequest
from app.services.approvals import execution as execution_module
from app.services.approvals.execution import EXECUTION_FAILED_MESSAGE, ApprovalExecutionService


def _result(tool_name: str, *, success: bool, error: str | None = None) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        success=success,
        output={"id": "f1"} if success else None,
        error=error,
        metadata=ToolExecutionMetadata(tool_name=tool_name, latency_ms=0.0),
    )


@pytest.fixture
def no_run_persistence(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    completed: list[tuple[Any, ...]] = []

    async def resume(_session: Any, _request: Any) -> None:
        return None

    async def complete(_session: Any, run_id: str, status: Any, *_a: Any, **kw: Any) -> None:
        completed.append((run_id, status, kw.get("error_summary")))

    monkeypatch.setattr(ApprovalExecutionService, "_resume_run", staticmethod(resume))
    monkeypatch.setattr(execution_module.RunPersistenceService, "complete_run", complete)
    return completed


def _request(tool_name: str) -> ApprovalRequest:
    return ApprovalRequest(
        id="ap-1", run_id="run-1", tool_name=tool_name, parameters={"file_id": "f1"}
    )


async def test_drive_tools_are_routed_with_the_approval_token(
    no_run_persistence: list[tuple[Any, ...]],
) -> None:
    seen: list[tuple[ToolInput, ToolContext]] = []

    async def drive(tool_input: ToolInput, context: ToolContext) -> ToolResult:
        seen.append((tool_input, context))
        return _result(tool_input.tool_name, success=True)

    service = ApprovalExecutionService(drive_execute=drive)
    outcome = await service.execute(None, _request("drive.rename_file"), "appr_tok", "user_a")  # type: ignore[arg-type]

    assert outcome.success is True
    assert seen[0][0].tool_name == "drive.rename_file"
    assert seen[0][0].arguments == {"file_id": "f1"}
    assert seen[0][1].approval_token == "appr_tok"


async def test_tool_errors_are_logged_but_the_user_sees_vietnamese(
    no_run_persistence: list[tuple[Any, ...]],
) -> None:
    async def failing(tool_input: ToolInput, _context: ToolContext) -> ToolResult:
        return _result(tool_input.tool_name, success=False, error="Calendar event not found.")

    service = ApprovalExecutionService(calendar_execute=failing)
    outcome = await service.execute(None, _request("calendar.update_event"), "t", "user_a")  # type: ignore[arg-type]

    assert outcome.success is False
    assert outcome.message == EXECUTION_FAILED_MESSAGE
    assert outcome.error == "Calendar event not found."
    assert no_run_persistence[0][2] == "Calendar event not found."


async def test_unknown_tool_family_fails_with_the_fixed_message(
    no_run_persistence: list[tuple[Any, ...]],
) -> None:
    outcome = await ApprovalExecutionService().execute(
        None,  # type: ignore[arg-type]
        _request("contacts.delete"),
        "t",
        "user_a",
    )
    assert outcome.success is False
    assert outcome.message == EXECUTION_FAILED_MESSAGE
    assert outcome.error is not None and "contacts.delete" in outcome.error
