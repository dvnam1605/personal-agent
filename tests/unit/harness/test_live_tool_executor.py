"""Live tool executor: read-only routing, writes refused, one session per call."""

from __future__ import annotations

from typing import Any

from app.domain.errors import ExternalServiceError
from app.domain.models.platform.tool import (
    ToolContext,
    ToolExecutionMetadata,
    ToolInput,
    ToolResult,
)
from app.harness.runtime import build_first_party_tool_registry
from app.harness.tool_executor import LiveToolExecutor

CONTEXT = ToolContext(run_id="r1", user_id="u1")


class _Session:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def __aenter__(self) -> _Session:
        self.log.append("open")
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.log.append("close")

    async def commit(self) -> None:
        self.log.append("commit")

    async def rollback(self) -> None:
        self.log.append("rollback")


def _executor(factories: dict[str, Any], log: list[str]) -> LiveToolExecutor:
    return LiveToolExecutor(build_first_party_tool_registry(), lambda: _Session(log), factories)  # type: ignore[arg-type]


async def test_read_tool_runs_for_the_calling_user_in_its_own_session() -> None:
    log: list[str] = []
    seen: dict[str, Any] = {}

    async def calendar(session: Any, user_id: str, tool_name: str) -> Any:
        seen.update(user_id=user_id, tool_name=tool_name)

        async def execute(tool_input: ToolInput, context: ToolContext) -> ToolResult:
            return ToolResult(
                tool_name=tool_input.tool_name,
                success=True,
                output=[],
                metadata=ToolExecutionMetadata(tool_name=tool_input.tool_name, latency_ms=0.0),
            )

        return execute

    result = await _executor({"calendar": calendar}, log).execute(
        ToolInput(tool_name="calendar.list_events", arguments={}), CONTEXT
    )
    assert result.success
    assert seen == {"user_id": "u1", "tool_name": "calendar.list_events"}
    assert log == ["open", "commit", "close"]


async def test_mutation_tools_are_refused_without_touching_a_provider() -> None:
    log: list[str] = []
    result = await _executor({}, log).execute(
        ToolInput(tool_name="gmail.create_draft", arguments={}), CONTEXT
    )
    assert not result.success and "approval" in (result.error or "")
    assert log == []


async def test_unknown_tools_are_refused() -> None:
    result = await _executor({}, []).execute(ToolInput(tool_name="calendar__x"), CONTEXT)
    assert not result.success and "not registered" in (result.error or "")


async def test_provider_errors_become_failed_results_and_roll_back() -> None:
    log: list[str] = []

    async def calendar(session: Any, user_id: str, tool_name: str) -> Any:
        raise ExternalServiceError("Google chưa kết nối.")

    result = await _executor({"calendar": calendar}, log).execute(
        ToolInput(tool_name="calendar.list_events"), CONTEXT
    )
    assert not result.success and result.error == "Google chưa kết nối."
    assert log == ["open", "rollback", "close"]
