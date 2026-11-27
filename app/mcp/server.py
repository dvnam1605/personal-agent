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


class UnknownToolError(LookupError):
    """The client called a tool this server does not declare."""


class MalformedResponseError(ValueError):
    """The assistant API answered with something other than the expected JSON shape."""


class AssistantApi:
    """HTTP calls to the assistant with the user's credentials from the environment."""

    def __init__(self, base_url: str, headers: dict[str, str], client: httpx.Client | None = None):
        self._base = base_url.rstrip("/")
        self._headers = headers
        self._client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=5.0))

    @classmethod
    def from_env(cls) -> AssistantApi:
        """Build a client from ``NOAT_BASE_URL`` and a token or API key in the environment."""
        headers: dict[str, str] = {}
        if token := os.environ.get("NOAT_TOKEN"):
            headers["Authorization"] = f"Bearer {token}"
        elif api_key := os.environ.get("NOAT_API_KEY"):
            headers["X-API-Key"] = api_key
            if user_id := os.environ.get("NOAT_USER_ID"):
                headers["X-User-ID"] = user_id
        return cls(os.environ.get("NOAT_BASE_URL", "http://127.0.0.1:8000"), headers)

    def request(self, method: str, path: str, body: Any = None) -> Any:
        """Send one request and return the decoded JSON body; non-JSON is malformed."""
        response = self._client.request(
            method, f"{self._base}{path}", json=body, headers=self._headers
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise MalformedResponseError("response is not JSON") from exc

    def request_object(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        """Like :meth:`request`, but the body must be a JSON object."""
        data = self.request(method, path, body)
        if not isinstance(data, dict):
            raise MalformedResponseError("expected a JSON object")
        return data

    def request_list(self, method: str, path: str, body: Any = None) -> list[Any]:
        """Like :meth:`request`, but the body must be a JSON list."""
        data = self.request(method, path, body)
        if not isinstance(data, list):
            raise MalformedResponseError("expected a JSON list")
        return data


def call_tool(api: AssistantApi, name: str, arguments: dict[str, Any]) -> str:
    """Run one tool and return its text result.

    Raises :class:`UnknownToolError` for an undeclared tool and
    :class:`MalformedResponseError` when the API answers in an unexpected shape, so
    the caller can turn each into the right JSON-RPC reply.
    """
    if name == "ask_assistant":
        result = api.request_object(
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
        data = api.request_object("GET", f"/notifications?unread_only={unread}&limit=20")
        items = data.get("items") or []
        if not isinstance(items, list):
            raise MalformedResponseError("notification items are not a list")
        if not items:
            return "Không có thông báo."
        return "\n\n".join(f"## {_field(item, 'title')}\n{_field(item, 'body')}" for item in items)
    if name == "list_memories":
        memories = api.request_list("GET", "/memories")
        lines = [f"- {_field(item, 'content')}" for item in memories]
        return "\n".join(lines) or "Chưa ghi nhớ gì."
    if name == "remember":
        content = str(arguments.get("content") or "")
        item = api.request_object("POST", "/memories", {"content": content})
        return f"Đã ghi nhớ: {_field(item, 'content')}"
    raise UnknownToolError(name)


def _field(item: Any, key: str) -> str:
    """Read a required text field of one API item, or report the response as malformed."""
    if not isinstance(item, dict) or key not in item:
        raise MalformedResponseError(f"missing field {key!r}")
    return str(item[key])


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
        arguments = (params.get("arguments") or {}) if isinstance(params, dict) else None
        if not isinstance(params, dict) or not isinstance(arguments, dict):
            return _error(msg_id, -32602, "Invalid params")
        name = str(params.get("name") or "")
        try:
            text = call_tool(api, name, arguments)
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except UnknownToolError:
            return _error(msg_id, -32602, f"Unknown tool: {name}")
        except httpx.HTTPError as exc:
            result = _tool_error(f"Assistant API error: {type(exc).__name__}")
        except MalformedResponseError as exc:
            result = _tool_error(f"Assistant API returned an unexpected response: {exc}")
    else:
        return _error(msg_id, -32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _tool_error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def serve(
    api: AssistantApi,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    log: Callable[[str], None] | None = None,
) -> None:
    """Read newline-delimited JSON-RPC from ``stdin`` and answer on ``stdout``.

    One bad message never ends the loop: parse errors, invalid requests and any
    unexpected failure while handling a call are answered with a JSON-RPC error.
    """
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply: dict[str, Any] | None = _error(None, -32700, "Parse error")
        else:
            if not isinstance(message, dict):
                reply = _error(None, -32600, "Invalid request")
            else:
                try:
                    reply = handle(api, message)
                except Exception as exc:  # noqa: BLE001 - keep the stdio server alive
                    if log is not None:
                        log(f"internal error: {type(exc).__name__}")
                    reply = _error(message.get("id"), -32603, "Internal error")
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
