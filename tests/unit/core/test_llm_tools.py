"""Gateway tool-calling backend: wire format, name mapping, retries and errors."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.core.llm_tools import GatewayChatBackend, from_wire_name, to_wire_name
from app.domain.errors import ExternalServiceError
from app.domain.models.platform.tool import ToolDefinition
from app.domain.models.supervisor.specialist import ChatMessage, ToolCallRequest

TOOL = ToolDefinition(
    name="calendar.list_events",
    description="List events",
    category="calendar",
    parameters_schema={"type": "object", "properties": {"start": {"type": "string"}}},
)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.llm, "openai_api_key", "test-key")


def _backend(handler: Any) -> tuple[GatewayChatBackend, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return handler(request)

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(record), **kwargs)

    return GatewayChatBackend(client_factory=factory, max_retries=1), sent


def _reply(message: dict[str, Any], usage: dict[str, int] | None = None) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": message}], "usage": usage or {}})


def test_names_round_trip_through_the_wire_format() -> None:
    wire = to_wire_name("calendar.list_events")
    assert wire == "calendar__list_events"
    assert from_wire_name(wire, {wire: "calendar.list_events"}) == "calendar.list_events"


async def test_tool_calls_are_parsed_and_mapped_back() -> None:
    backend, sent = _backend(
        lambda _r: _reply(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "calendar__list_events",
                            "arguments": '{"start": "x"}',
                        },
                    }
                ],
            },
            {"prompt_tokens": 11, "completion_tokens": 3},
        )
    )
    turn = await backend.complete([ChatMessage(role="user", content="lịch mai?")], [TOOL])
    assert turn.tool_calls == [
        ToolCallRequest(
            tool_name="calendar.list_events", arguments={"start": "x"}, call_id="call_1"
        )
    ]
    assert (turn.prompt_tokens, turn.completion_tokens) == (11, 3)
    assert sent[0]["tools"][0]["function"]["name"] == "calendar__list_events"
    assert sent[0]["stream"] is False


async def test_transcript_keeps_tool_calls_paired_with_results() -> None:
    backend, sent = _backend(lambda _r: _reply({"content": "Ngày mai có họp."}))
    call = ToolCallRequest(tool_name="calendar.list_events", arguments={}, call_id="c1")
    turn = await backend.complete(
        [
            ChatMessage(role="user", content="lịch mai?"),
            ChatMessage(role="assistant", tool_calls=[call]),
            ChatMessage(role="tool", content="[]", tool_call_id="c1"),
        ],
        [TOOL],
    )
    assert turn.text == "Ngày mai có họp."
    assistant, tool = sent[0]["messages"][1], sent[0]["messages"][2]
    assert assistant["tool_calls"][0]["id"] == "c1"
    assert assistant["tool_calls"][0]["function"]["name"] == "calendar__list_events"
    assert tool == {"role": "tool", "tool_call_id": "c1", "content": "[]"}


async def test_a_dropped_connection_is_retried_once() -> None:
    attempts = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ReadError("dropped", request=request)
        return _reply({"content": "ok"})

    backend, _ = _backend(flaky)
    assert (await backend.complete([ChatMessage(role="user", content="hi")])).text == "ok"
    assert attempts["n"] == 2


async def test_http_errors_raise_for_the_caller_to_fall_back() -> None:
    backend, _ = _backend(lambda _r: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(ExternalServiceError):
        await backend.complete([ChatMessage(role="user", content="hi")])


def test_assistant_messages_may_be_empty_only_with_tool_calls() -> None:
    with pytest.raises(ValueError):
        ChatMessage(role="assistant", content="")
    with pytest.raises(ValueError):
        ChatMessage(role="user", tool_calls=[ToolCallRequest(tool_name="x")])
