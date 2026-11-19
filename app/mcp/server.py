"""MCP server (stdio) exposing the assistant to other agents such as Claude Desktop.

It is a thin client of the assistant's own HTTP API, authenticated as the user, so every
answer goes through the same routing, owner scoping and safety checks as the web app.
There is deliberately no approval tool: a write the assistant proposes is approved by
the user in the web app or on Telegram, never by another agent.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any, TextIO

import httpx

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "noat-assistant", "version": "1.0.0"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "ask_assistant",
        "description": (
            "Ask the user's personal assistant (calendar, Gmail, internal documents, tasks, "
            "memory, web on request). Writes are only proposed; the user approves them in the app."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The request, in Vietnamese or English",
                }
            },
            "required": ["question"],
        },
    },
    {
        "name": "list_notifications",
        "description": "Recent notifications the assistant prepared (briefings, meeting dossiers, alerts).",
        "inputSchema": {
            "type": "object",
            "properties": {"unread_only": {"type": "boolean", "default": True}},
        },
    },
    {
        "name": "list_memories",
        "description": "What the user asked the assistant to remember.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "remember",
        "description": "Ask the assistant to remember a fact or preference for the user.",
        "inputSchema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
    },
]


class AssistantApi:
    """HTTP calls to the assistant with the user's credentials from the environment."""

    def __init__(self, base_url: str, headers: dict[str, str], client: httpx.Client | None = None):
        self._base = base_url.rstrip("/")
        self._headers = headers
        self._client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=5.0))

    @classmethod
    def from_env(cls) -> AssistantApi:
        headers: dict[str, str] = {}
        if token := os.environ.get("NOAT_TOKEN"):
            headers["Authorization"] = f"Bearer {token}"
        elif api_key := os.environ.get("NOAT_API_KEY"):
            headers["X-API-Key"] = api_key
            if user_id := os.environ.get("NOAT_USER_ID"):
                headers["X-User-ID"] = user_id
        return cls(os.environ.get("NOAT_BASE_URL", "http://127.0.0.1:8000"), headers)

    def request(self, method: str, path: str, body: Any = None) -> Any:
        response = self._client.request(
            method, f"{self._base}{path}", json=body, headers=self._headers
        )
        response.raise_for_status()
        return response.json()


def call_tool(api: AssistantApi, name: str, arguments: dict[str, Any]) -> str:
    """Run one tool and return its text result."""
    if name == "ask_assistant":
        result = api.request(
            "POST", "/query", {"query": str(arguments.get("question") or "")[:4000]}
        )
        text = str(result.get("message") or "")
        if result.get("approval_id"):
            text += (
                "\n\n[Cần người dùng duyệt trong ứng dụng Noat hoặc Telegram — "
                f"approval_id {result['approval_id']}. Không thể duyệt từ đây.]"
            )
        return text
    if name == "list_notifications":
        unread = "true" if arguments.get("unread_only", True) else "false"
        data = api.request("GET", f"/notifications?unread_only={unread}&limit=20")
        items = data.get("items") or []
        if not items:
            return "Không có thông báo."
        return "\n\n".join(f"## {item['title']}\n{item['body']}" for item in items)
    if name == "list_memories":
        items = api.request("GET", "/memories")
        return "\n".join(f"- {item['content']}" for item in items) or "Chưa ghi nhớ gì."
    if name == "remember":
        item = api.request("POST", "/memories", {"content": str(arguments.get("content") or "")})
        return f"Đã ghi nhớ: {item['content']}"
    raise KeyError(name)


def handle(api: AssistantApi, message: dict[str, Any]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message; ``None`` for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        return None
    if method == "initialize":
        result: Any = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        try:
            text = call_tool(api, name, params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except KeyError:
            return _error(msg_id, -32602, f"Unknown tool: {name}")
        except httpx.HTTPError as exc:
            result = {
                "content": [{"type": "text", "text": f"Assistant API error: {type(exc).__name__}"}],
                "isError": True,
            }
    else:
        return _error(msg_id, -32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def serve(
    api: AssistantApi,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    log: Callable[[str], None] | None = None,
) -> None:
    """Read newline-delimited JSON-RPC from ``stdin`` and answer on ``stdout``."""
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply: dict[str, Any] | None = _error(None, -32700, "Parse error")
        else:
            reply = (
                handle(api, message)
                if isinstance(message, dict)
                else _error(None, -32600, "Invalid request")
            )
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
