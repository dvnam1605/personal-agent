"""Telegram bot: chat with the assistant, approve actions with buttons, receive notifications."""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import AppError, ExternalServiceError
from app.infrastructure.db.models import Notification
from app.services.approvals.decisions import approve_and_execute, deny
from app.services.approvals.execution import ApprovalExecutionService
from app.services.telegram.linking import TelegramLinkService

logger = structlog.get_logger(__name__)

HELP = (
    "Xin chào! Tôi là trợ lý Noat.\n"
    "1. Mở ứng dụng → Cài đặt → Telegram → «Tạo mã liên kết».\n"
    "2. Gửi cho tôi: /link <mã 6 số>.\n"
    "Sau đó hỏi tôi như trong ứng dụng; việc cần duyệt sẽ có nút Duyệt / Từ chối.\n"
    "/unlink để hủy liên kết."
)
NOT_LINKED = "Chat này chưa liên kết tài khoản. Gửi /start để xem cách liên kết."
MAX_LINK_ATTEMPTS = 5
LINK_WINDOW_SECONDS = 600
MAX_FORWARDED = 10
_LINK = re.compile(r"^/link(?:@\w+)?\s+(\d{6})\s*$")


def to_plain(markdown: str) -> str:
    """Telegram gets plain text: drop emphasis, unescape, show links as "text (url)"."""
    text = re.sub(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", r"\1 (\2)", markdown)
    text = re.sub(r"<(https?://[^>\s]+)>", r"\1", text)
    text = text.replace("**", "").replace("__", "")
    return re.sub(r"\\([\\`*_{}\[\]()<>#+!|~>-])", r"\1", text)


class TelegramBot:
    """Serve linked private chats through the same orchestrator and approvals as the web app.

    Unlinked chats only get linking instructions. Approve/deny buttons carry the approval
    id; pressing one is checked against the chat's account exactly like the web route.
    Linking attempts are rate-limited per chat against code guessing.
    """

    def __init__(
        self,
        client: Any,
        orchestrator: Any,
        session_factory: async_sessionmaker[AsyncSession],
        execution_service: Callable[[], ApprovalExecutionService] = ApprovalExecutionService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._orchestrator = orchestrator
        self._session_factory = session_factory
        self._execution_service = execution_service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._link_attempts: dict[int, list[float]] = defaultdict(list)

    async def handle_update(self, update: dict[str, Any]) -> None:
        async with self._session_factory() as session:
            try:
                if isinstance(update.get("callback_query"), dict):
                    await self._on_button(session, update["callback_query"])
                elif isinstance(update.get("message"), dict):
                    await self._on_message(session, update["message"])
                await session.commit()
            except Exception:  # noqa: BLE001 - one bad update must not stop the bot
                await session.rollback()
                logger.exception("telegram_update_failed")

    async def _on_message(self, session: AsyncSession, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id, text = chat.get("id"), str(message.get("text") or "").strip()
        if not isinstance(chat_id, int) or chat.get("type") != "private" or not text:
            return
        if text.startswith(("/start", "/help")):
            await self._client.send_message(chat_id, HELP)
            return
        if text.startswith("/link"):
            await self._client.send_message(chat_id, await self._link(session, chat_id, text))
            return
        link = await TelegramLinkService.by_chat(session, chat_id)
        if link is None:
            await self._client.send_message(chat_id, NOT_LINKED)
            return
        if text.startswith("/unlink"):
            await TelegramLinkService.unlink(session, link.user_id)
            await self._client.send_message(chat_id, "Đã hủy liên kết.")
            return
        await self._ask(session, chat_id, link.user_id, link.conversation_id, text)

    async def _ask(
        self,
        session: AsyncSession,
        chat_id: int,
        user_id: str,
        conversation_id: str | None,
        text: str,
    ) -> None:
        try:
            result = await self._orchestrator.handle(
                session, user_id, text, conversation_id=conversation_id
            )
        except AppError as exc:
            await self._client.send_message(chat_id, f"Không xử lý được: {exc.message}")
            return
        buttons = None
        if result.approval_id:
            buttons = [
                [
                    {"text": "✅ Duyệt", "callback_data": f"a:{result.approval_id}"},
                    {"text": "✖️ Từ chối", "callback_data": f"d:{result.approval_id}"},
                ]
            ]
        await self._client.send_message(chat_id, to_plain(result.message), buttons)

    async def _link(self, session: AsyncSession, chat_id: int, text: str) -> str:
        now = time.monotonic()
        attempts = [t for t in self._link_attempts[chat_id] if now - t < LINK_WINDOW_SECONDS]
        if len(attempts) >= MAX_LINK_ATTEMPTS:
            self._link_attempts[chat_id] = attempts
            return "Thử quá nhiều lần. Đợi 10 phút rồi thử lại."
        match = _LINK.match(text)
        link = (
            await TelegramLinkService.redeem(session, match.group(1), chat_id, now=self._clock())
            if match
            else None
        )
        if link is None:
            self._link_attempts[chat_id] = [*attempts, now]
            return "Mã không đúng hoặc đã hết hạn. Tạo mã mới trong ứng dụng rồi gửi /link <mã>."
        self._link_attempts.pop(chat_id, None)
        return "Đã liên kết! Bạn có thể hỏi tôi ngay tại đây."

    async def _on_button(self, session: AsyncSession, callback: dict[str, Any]) -> None:
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        data = str(callback.get("data") or "")
        callback_id = str(callback.get("id") or "")
        if not isinstance(chat_id, int):
            return
        link = await TelegramLinkService.by_chat(session, chat_id)
        if link is None or ":" not in data:
            await self._client.answer_callback(callback_id, NOT_LINKED)
            return
        action, target = data.split(":", 1)
        if action == "n":
            await self._client.answer_callback(callback_id, "Đang xử lý…")
            await self._suggested(session, link.user_id, link.conversation_id, chat_id, target)
            return
        if action == "a":
            outcome = await approve_and_execute(
                session, target, link.user_id, self._execution_service()
            )
        elif action == "d":
            outcome = await deny(session, target, link.user_id)
        else:
            return
        await self._client.answer_callback(callback_id, outcome.message)
        if outcome.ok and isinstance(message.get("message_id"), int):
            await self._client.clear_buttons(chat_id, message["message_id"])
        await self._client.send_message(chat_id, outcome.message)

    async def _suggested(
        self,
        session: AsyncSession,
        user_id: str,
        conversation_id: str | None,
        chat_id: int,
        notification_id: str,
    ) -> None:
        notification = await session.get(Notification, notification_id)
        if notification is None or notification.user_id != user_id:
            return
        query = (notification.data or {}).get("suggested_query")
        if isinstance(query, str) and query:
            await self._ask(session, chat_id, user_id, conversation_id, query)

    async def forward_notifications(self) -> int:
        """Send each linked chat the notifications created since the last forward."""
        sent = 0
        async with self._session_factory() as session:
            for link in await TelegramLinkService.linked(session):
                if link.chat_id is None:
                    continue
                since = link.last_notified_at or link.linked_at or self._clock()
                rows = await session.scalars(
                    select(Notification)
                    .where(Notification.user_id == link.user_id, Notification.created_at > since)
                    .order_by(Notification.created_at)
                    .limit(MAX_FORWARDED)
                )
                for notification in rows.all():
                    buttons = None
                    label = (notification.data or {}).get("action_label")
                    if (notification.data or {}).get("suggested_query"):
                        buttons = [
                            [
                                {
                                    "text": str(label or "Làm ngay"),
                                    "callback_data": f"n:{notification.id}",
                                }
                            ]
                        ]
                    try:
                        await self._client.send_message(
                            link.chat_id,
                            f"🔔 {to_plain(notification.title)}\n\n{to_plain(notification.body)}",
                            buttons,
                        )
                    except ExternalServiceError:
                        break
                    link.last_notified_at = notification.created_at
                    sent += 1
            await session.commit()
        return sent

    async def run(self, stop_event: asyncio.Event) -> None:
        """Long-poll Telegram until ``stop_event`` is set; forward notifications between polls."""
        offset: int | None = None
        while not stop_event.is_set():
            try:
                updates = await self._client.get_updates(offset)
            except ExternalServiceError:
                logger.warning("telegram_poll_failed")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=5.0)
                except TimeoutError:
                    pass
                continue
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                await self.handle_update(update)
            try:
                await self.forward_notifications()
            except Exception:  # noqa: BLE001 - forwarding is best effort
                logger.exception("telegram_forward_failed")
