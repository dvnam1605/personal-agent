"""Minimal Telegram Bot API client: long-poll updates, send messages, answer button presses."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.settings.telegram import TelegramSettings
from app.domain.errors import ExternalServiceError

MAX_MESSAGE_LEN = 4000


class TelegramClient:
    """Calls the few Bot API methods the assistant needs; errors become ExternalServiceError.

    The bot token is part of every URL, so URLs are never logged and exceptions carry
    only the method name.
    """

    def __init__(self, settings: TelegramSettings, client: httpx.AsyncClient | None = None) -> None:
        if not settings.bot_token:
            raise ValueError("Telegram requires TELEGRAM__BOT_TOKEN.")
        self._settings = settings
        self._client = client

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": self._settings.poll_timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._call(
            "getUpdates", payload, read=self._settings.poll_timeout_seconds + 10
        )
        return (
            [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []
        )

    async def send_message(
        self, chat_id: int, text: str, buttons: list[list[dict[str, str]]] | None = None
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:MAX_MESSAGE_LEN],
            "disable_web_page_preview": True,
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        await self._call("sendMessage", payload)

    async def answer_callback(self, callback_id: str, text: str) -> None:
        await self._call(
            "answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]}
        )

    async def clear_buttons(self, chat_id: int, message_id: int) -> None:
        await self._call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        )

    async def _call(self, method: str, payload: dict[str, Any], *, read: float = 15.0) -> Any:
        url = f"{self._settings.api_base.rstrip('/')}/bot{self._settings.bot_token}/{method}"
        try:
            if self._client is not None:
                response = await self._client.post(url, json=payload)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(read, connect=5.0)) as client:
                    response = await client.post(url, json=payload)
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ExternalServiceError(f"Telegram {method} failed: {type(exc).__name__}") from exc
        if not isinstance(body, dict) or not body.get("ok"):
            raise ExternalServiceError(f"Telegram {method} was rejected.")
        return body.get("result")
