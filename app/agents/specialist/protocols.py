"""First-party seams for the specialist runtime (spec P11).

``ChatBackend`` and ``ToolExecutor`` are injectable protocols: unit tests use
scripted fakes, production wires real LLM clients and tool dispatch later
(P12+). Keeping them structural preserves the dependency boundary
(``app/agents/`` sees model/tool interfaces only, never orchestration policy
from a prebuilt package).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models.platform.tool import ToolContext, ToolDefinition, ToolInput, ToolResult
from app.domain.models.supervisor.specialist import AssistantTurn, ChatMessage


@runtime_checkable
class ChatBackend(Protocol):
    """Single LLM call: messages + gated tool schemas -> one assistant turn."""

    async def complete(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
    ) -> AssistantTurn: ...


@runtime_checkable
class ToolExecutor(Protocol):
    """Execute one gated tool call and return the normalized result."""

    async def execute(self, tool_input: ToolInput, context: ToolContext) -> ToolResult: ...
