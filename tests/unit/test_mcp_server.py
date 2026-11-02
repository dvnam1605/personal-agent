"""Unit tests for the MCP stdio server."""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest

from app.mcp.server import TOOLS, AssistantApi, handle, serve


def _api(handler: Any, headers: dict[str, str] | None = None) -> AssistantApi:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AssistantApi(
        "http://noat.test/", headers or {"Authorization": "Bearer t"}, client=client
    )


def _backend(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer t"
    if request.url.path == "/query":
        body = json.loads(request.content)
        if "duyệt" in body["query"]:
            return httpx.Response(200, json={"message": "Thêm việc “x”.", "approval_id": "ap-1"})
        return httpx.Response(
            200, json={"message": f"Trả lời: {body['query']}", "approval_id": None}
        )
    if request.url.path == "/notifications":
        assert request.url.params["unread_only"] == "true"
        return httpx.Response(200, json={"items": [{"title": "Bản tin", "body": "3 lịch"}]})
    if request.url.path == "/memories" and request.method == "GET":
        return httpx.Response(200, json=[{"content": "anh Nam là nam@x.vn"}])
    if request.url.path == "/memories":
        return httpx.Response(201, json={"content": json.loads(request.content)["content"]})
    return httpx.Response(500)


def _call(api: AssistantApi, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    reply = handle(
        api,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert reply is not None
    return reply


def test_initialize_list_and_tools() -> None:
    api = _api(_backend)
    init = handle(api, {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    assert init is not None and init["result"]["capabilities"] == {"tools": {}}
    assert handle(api, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    listed = handle(api, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert names == [tool["name"] for tool in TOOLS] and not any("approv" in n for n in names)

    assert (
        _call(api, "ask_assistant", {"question": "Lịch mai?"})["result"]["content"][0]["text"]
        == "Trả lời: Lịch mai?"
    )
    gated = _call(api, "ask_assistant", {"question": "cần duyệt"})["result"]["content"][0]["text"]
    assert "Không thể duyệt từ đây" in gated and "ap-1" in gated
    assert (
        "## Bản tin\n3 lịch" in _call(api, "list_notifications", {})["result"]["content"][0]["text"]
    )
    assert (
        _call(api, "list_memories", {})["result"]["content"][0]["text"] == "- anh Nam là nam@x.vn"
    )
    assert (
        _call(api, "remember", {"content": "x"})["result"]["content"][0]["text"] == "Đã ghi nhớ: x"
    )


def test_errors() -> None:
    api = _api(lambda _r: httpx.Response(500))
    failed = _call(api, "ask_assistant", {"question": "x"})
    assert failed["result"]["isError"] is True
    assert _call(api, "approve", {})["error"]["code"] == -32602
    unknown = handle(api, {"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
    assert unknown is not None and unknown["error"]["code"] == -32601


def test_serve_reads_lines_and_reports_parse_errors() -> None:
    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nnot json\n[1]\n')
    stdout = io.StringIO()
    serve(_api(_backend), stdin=stdin, stdout=stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert replies[1]["error"]["code"] == -32700 and replies[2]["error"]["code"] == -32600


def test_credentials_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOAT_TOKEN", raising=False)
    monkeypatch.setenv("NOAT_API_KEY", "k")
    monkeypatch.setenv("NOAT_USER_ID", "u1")
    api = AssistantApi.from_env()
    assert api._headers == {"X-API-Key": "k", "X-User-ID": "u1"}
    monkeypatch.setenv("NOAT_TOKEN", "tok")
    assert AssistantApi.from_env()._headers == {"Authorization": "Bearer tok"}
