"""Shared scripted doubles for specialist runtime tests (no LLM, no network)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.domain.enums import ActionClass, ActionRiskLevel, Domain, ExecutionMode
from app.domain.models import (
    AgentDefinition,
    ToolDefinition,
    ToolExecutionMetadata,
    ToolResult,
)
from app.domain.models.supervisor.specialist import AssistantTurn, ChatMessage, ToolCallRequest


def make_agent(
    name: str = "TestSpecialist",
    *,
    mode: ExecutionMode = ExecutionMode.BOUNDED_REACT,
    categories: list[str] | None = None,
    delegation_allowed: bool = True,
    max_child_depth: int | None = 3,
) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        description="Specialist test double",
        domain=Domain.GENERAL,
        capabilities=[],
        allowed_tool_categories=categories if categories is not None else ["test"],
        default_execution_mode=mode,
        delegation_allowed=delegation_allowed,
        max_child_depth=max_child_depth,
    )


def make_read_tool(name: str = "test.search") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Read-only test tool",
        category="test",
        capabilities=[name],
    )


def make_mutation_tool(name: str = "test.write") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Mutation test tool",
        category="test",
        capabilities=[name],
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        is_mutation=True,
        action_class=ActionClass.SAFE_WRITE,
    )


def ok_result(tool_name: str, output: Any = "ok") -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        success=True,
        output=output,
        metadata=ToolExecutionMetadata(tool_name=tool_name, latency_ms=1.0),
    )


def err_result(tool_name: str, error: str = "boom") -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        success=False,
        error=error,
        metadata=ToolExecutionMetadata(tool_name=tool_name, latency_ms=1.0),
    )


def text_turn(text: str) -> AssistantTurn:
    return AssistantTurn(text=text, tool_calls=[])


def calls_turn(*calls: tuple[str, dict[str, Any]]) -> AssistantTurn:
    return AssistantTurn(
        text="",
        tool_calls=[ToolCallRequest(tool_name=name, arguments=args) for name, args in calls],
    )


def report_turn(
    status: str = "success",
    summary: str = "done",
    extra: dict[str, Any] | None = None,
) -> AssistantTurn:
    arguments: dict[str, Any] = {"status": status, "summary": summary}
    if extra:
        arguments.update(extra)
    return calls_turn(("specialist.report", arguments))


class ScriptedChat:
    """Pop scripted turns; records prompts for assertion.

    Strict by default: exhausting the script raises instead of silently
    repeating the last turn (which could mask broken loop logic). Pass
    ``repeat_last=True`` for runs that legitimately need endless turns
    (max-steps / no-progress / breaker tests).
    """

    def __init__(self, turns: list[AssistantTurn], *, repeat_last: bool = False) -> None:
        self._turns = list(turns)
        self._repeat_last = repeat_last
        self._consumed_single = False
        self.prompts: list[list[ChatMessage]] = []
        self.tool_schemas_seen: list[list[str]] = []

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition]
    ) -> AssistantTurn:
        self.prompts.append(list(messages))
        self.tool_schemas_seen.append([tool.name for tool in tools])
        if len(self._turns) > 1:
            return self._turns.pop(0)
        if not self._turns or (self._consumed_single and not self._repeat_last):
            raise AssertionError(
                "ScriptedChat exhausted: the runner asked for more LLM turns "
                "than scripted (pass repeat_last=True for unbounded loops)."
            )
        self._consumed_single = True
        return self._turns[0]


class DictExecutor:
    """Dispatch tool calls to per-tool handlers; records every invocation."""

    def __init__(self, handlers: dict[str, Callable[[dict[str, Any]], ToolResult]]) -> None:
        self._handlers = handlers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool_input: Any, context: Any) -> ToolResult:
        del context
        self.calls.append((tool_input.tool_name, dict(tool_input.arguments)))
        handler = self._handlers.get(tool_input.tool_name)
        if handler is None:
            return err_result(tool_input.tool_name, "no handler registered")
        return handler(dict(tool_input.arguments))
