"""Tool-calling chat backend over the OpenAI-compatible LLM gateway.

The agentic harness (supervisor planner and ReAct specialists) talks to the model
through ``ChatBackend.complete(messages, tools)``. This module implements that seam
with native OpenAI tool calls, which the gateway supports.

Tool names in the app are dotted (``calendar.list_events``) but OpenAI function names
must match ``^[a-zA-Z0-9_-]+$``, so dots travel as ``__`` and are mapped back on the
way in. A name the model invents is passed through unmapped; the specialist's tool
gate rejects it and tells the model, so nothing outside the offered list can run.

Unlike ``app.core.llm`` this backend raises ``ExternalServiceError`` on failure: the
harness caller falls back to the rule-based path instead of guessing an answer.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from app.core.config import settings
from app.core.http import llm_http_timeout
from app.domain.errors import ExternalServiceError
from app.domain.models.platform.tool import ToolDefinition
from app.domain.models.supervisor.specialist import AssistantTurn, ChatMessage, ToolCallRequest

logger = logging.getLogger(__name__)

_WIRE_SEPARATOR = "__"


def to_wire_name(name: str) -> str:
    """Encode an app tool name as an OpenAI function name."""
    return name.replace(".", _WIRE_SEPARATOR)


def from_wire_name(name: str, known: dict[str, str]) -> str:
    """Decode a function name the model returned, preferring the names actually offered."""
    return known.get(name) or name.replace(_WIRE_SEPARATOR, ".")


def _tool_schema(tool: ToolDefinition) -> dict[str, Any]:
    parameters = tool.parameters_schema or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": to_wire_name(tool.name),
            "description": tool.description,
            "parameters": parameters,
        },
    }


def _wire_message(message: ChatMessage) -> dict[str, Any]:
    """Translate one transcript message, keeping tool calls paired with their results."""
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": to_wire_name(call.tool_name),
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ],
        }
    if message.role == "tool" and message.tool_call_id:
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.role == "tool":
        # A tool result without a call id cannot be paired natively; show it as context.
        return {"role": "user", "content": f"[tool result]\n{message.content}"}
    return {"role": message.role, "content": message.content}


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_turn(payload: dict[str, Any], known: dict[str, str]) -> AssistantTurn:
    """Build an ``AssistantTurn`` from a chat-completions response body."""
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ExternalServiceError("LLM gateway returned no message.", service_name="llm") from exc
    calls: list[ToolCallRequest] = []
    for index, raw in enumerate(message.get("tool_calls") or []):
        function = (raw or {}).get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        calls.append(
            ToolCallRequest(
                tool_name=from_wire_name(name, known),
                arguments=_parse_arguments(function.get("arguments")),
                call_id=str(raw.get("id") or f"call_{index}"),
            )
        )
    usage = payload.get("usage") or {}
    return AssistantTurn(
        text=str(message.get("content") or "").strip(),
        tool_calls=calls,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )


class GatewayChatBackend:
    """``ChatBackend`` / ``PlannerChatBackend`` implementation over the LLM gateway.

    One non-streaming chat-completions call per turn. A dropped connection is retried
    ``max_retries`` times; any other failure raises ``ExternalServiceError``.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout_seconds: float | None = None,
        temperature: float = 0.0,
        max_retries: int = 1,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._model = model or settings.llm.primary_model
        self._timeout = timeout_seconds or settings.timeouts.llm_request_seconds
        self._temperature = temperature
        self._max_retries = max(0, max_retries)
        self._client_factory = client_factory

    async def complete(
        self,
        messages: list[ChatMessage],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> AssistantTurn:
        """Send the transcript and the offered tools; return text and tool calls."""
        api_key = str(settings.llm.openai_api_key or "")
        if not api_key:
            raise ExternalServiceError("LLM gateway API key is not configured.", service_name="llm")
        offered = list(tools or [])
        known = {to_wire_name(tool.name): tool.name for tool in offered}
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [_wire_message(message) for message in messages],
            "temperature": self._temperature,
            # The gateway answers with SSE unless told otherwise.
            "stream": False,
        }
        if offered:
            body["tools"] = [_tool_schema(tool) for tool in offered]
        payload = await self._post(body, api_key)
        return parse_turn(payload, known)

    async def _post(self, body: dict[str, Any], api_key: str) -> dict[str, Any]:
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                async with self._client_factory(timeout=llm_http_timeout(self._timeout)) as client:
                    response = await client.post(
                        settings.llm.chat_completions_url(),
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=body,
                    )
                    response.raise_for_status()
                    data = response.json()
            except httpx.TransportError as exc:
                if attempt < attempts:
                    logger.warning("llm_tools_transport_retry attempt=%s error=%s", attempt, exc)
                    continue
                raise ExternalServiceError("LLM gateway unreachable.", service_name="llm") from exc
            except (httpx.HTTPStatusError, ValueError) as exc:
                raise ExternalServiceError("LLM gateway call failed.", service_name="llm") from exc
            if not isinstance(data, dict):
                raise ExternalServiceError(
                    "LLM gateway returned a non-object body.", service_name="llm"
                )
            return data
        raise ExternalServiceError("LLM gateway unreachable.", service_name="llm")


__all__ = ["GatewayChatBackend", "from_wire_name", "parse_turn", "to_wire_name"]
