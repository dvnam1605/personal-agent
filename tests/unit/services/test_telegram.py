"""Unit tests for the Telegram channel: linking, chat, approval buttons, notifications."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user_id
from app.api.routes.telegram import router as telegram_router
from app.core.settings.telegram import TelegramSettings
from app.domain.errors import ExternalServiceError
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import TelegramLink, User
from app.infrastructure.db.session import get_db_session
from app.integrations.google_tasks import TaskItem
from app.integrations.telegram import TelegramClient
from app.services.approvals.execution import ApprovalExecutionService
from app.services.proactive import NotificationService
from app.services.routing.query import QueryOrchestrator
from app.services.telegram.bot import TelegramBot, to_plain
from app.services.telegram.linking import TelegramLinkService
from app.tools.google_tasks import GoogleTasksTools

NOW = datetime(2026, 10, 27, 2, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Any]] = []
        self.answers: list[str] = []
        self.cleared: list[int] = []

    async def send_message(self, chat_id: int, text: str, buttons: Any = None) -> None:
        self.sent.append((chat_id, text, buttons))

    async def answer_callback(self, callback_id: str, text: str) -> None:
        self.answers.append(text)

    async def clear_buttons(self, chat_id: int, message_id: int) -> None:
        self.cleared.append(message_id)


class FakeTasks:
    def __init__(self) -> None:
        self.created: list[str] = []

    async def list_tasks(self, **_kwargs: Any) -> list[TaskItem]:
        return [TaskItem(id="t1", title="Gọi anh Nam", due=date(2026, 10, 20))]

    async def insert_task(self, title: str, **_kwargs: Any) -> TaskItem:
        self.created.append(title)
        return TaskItem(id="new", title=title)


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all([User(id="user_a", email="a@x.vn"), User(id="user_b", email="b@x.vn")])
        await session.commit()
    yield session_factory
    await engine.dispose()


def _message(chat_id: int, text: str, kind: str = "private") -> dict[str, Any]:
    return {"message": {"chat": {"id": chat_id, "type": kind}, "text": text}}


def _button(chat_id: int, data: str) -> dict[str, Any]:
    return {
        "callback_query": {
            "id": "cb",
            "data": data,
            "message": {"message_id": 7, "chat": {"id": chat_id, "type": "private"}},
        }
    }


async def _code(factory: async_sessionmaker[AsyncSession], user_id: str) -> str:
    async with factory() as session:
        code, _ = await TelegramLinkService.issue_code(session, user_id, ttl=timedelta(minutes=10))
        await session.commit()
    return code


def _bot(
    factory: async_sessionmaker[AsyncSession], tasks: FakeTasks
) -> tuple[TelegramBot, FakeClient]:
    async def tasks_for_user(*_args: Any) -> FakeTasks:
        return tasks

    client = FakeClient()
    orchestrator = QueryOrchestrator(tasks_for_user=tasks_for_user, clock=lambda: NOW)
    bot = TelegramBot(
        client,
        orchestrator,
        factory,
        execution_service=lambda: ApprovalExecutionService(
            tasks_execute=GoogleTasksTools(tasks).execute  # type: ignore[arg-type]
        ),
        clock=lambda: NOW,
    )
    return bot, client


async def test_linking_chat_and_approving_from_buttons(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tasks = FakeTasks()
    bot, client = _bot(factory, tasks)

    await bot.handle_update(_message(100, "/start"))
    await bot.handle_update(_message(100, "việc cần làm?"))
    await bot.handle_update(_message(-5, "việc cần làm?", kind="group"))
    assert client.sent[0][1].startswith("Xin chào") and "chưa liên kết" in client.sent[1][1]
    assert len(client.sent) == 2

    await bot.handle_update(_message(100, "/link 000000"))
    assert "Mã không đúng" in client.sent[-1][1]
    await bot.handle_update(_message(100, f"/link {await _code(factory, 'user_a')}"))
    assert client.sent[-1][1].startswith("Đã liên kết")

    await bot.handle_update(_message(100, "việc cần làm?"))
    assert "Gọi anh Nam — **quá hạn**" not in client.sent[-1][1]
    assert "Gọi anh Nam — quá hạn" in client.sent[-1][1]

    await bot.handle_update(_message(100, "Thêm việc: gửi hợp đồng hạn thứ 6"))
    buttons = client.sent[-1][2]
    approve = buttons[0][0]["callback_data"]
    assert approve.startswith("a:") and buttons[0][1]["callback_data"].startswith("d:")

    # Another linked account cannot press this chat's button.
    await bot.handle_update(_message(200, f"/link {await _code(factory, 'user_b')}"))
    await bot.handle_update(_button(200, approve))
    assert client.answers[-1] == "Không tìm thấy yêu cầu này."
    assert tasks.created == []

    await bot.handle_update(_button(100, approve))
    assert tasks.created == ["gửi hợp đồng"]
    assert client.cleared == [7] and "Đã thêm việc" in client.answers[-1]
    await bot.handle_update(_button(100, approve))
    assert "đã được" in client.answers[-1]

    await bot.handle_update(_message(100, "/unlink"))
    await bot.handle_update(_message(100, "việc cần làm?"))
    assert "chưa liên kết" in client.sent[-1][1]


async def test_link_attempts_are_rate_limited(factory: async_sessionmaker[AsyncSession]) -> None:
    bot, client = _bot(factory, FakeTasks())
    for _ in range(5):
        await bot.handle_update(_message(100, "/link 123456"))
    code = await _code(factory, "user_a")
    await bot.handle_update(_message(100, f"/link {code}"))
    assert "Thử quá nhiều lần" in client.sent[-1][1]


async def test_expired_code_and_chat_moving_accounts(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        code, _ = await TelegramLinkService.issue_code(
            session, "user_a", ttl=timedelta(minutes=10), now=NOW - timedelta(hours=1)
        )
        assert await TelegramLinkService.redeem(session, code, 100, now=NOW) is None
        code_a, _ = await TelegramLinkService.issue_code(
            session, "user_a", ttl=timedelta(minutes=10)
        )
        code_b, _ = await TelegramLinkService.issue_code(
            session, "user_b", ttl=timedelta(minutes=10)
        )
        assert await TelegramLinkService.redeem(session, code_a, 100) is not None
        link_b = await TelegramLinkService.redeem(session, code_b, 100)
        assert link_b is not None and link_b.user_id == "user_b"
        link_a = await TelegramLinkService.by_user(session, "user_a")
        assert link_a is not None and link_a.chat_id is None
        assert await TelegramLinkService.redeem(session, code_b, 100) is None


async def test_notifications_are_forwarded_once_with_their_action(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, client = _bot(factory, FakeTasks())
    await bot.handle_update(_message(100, f"/link {await _code(factory, 'user_a')}"))
    async with factory() as session:
        notification = await NotificationService.create(
            session,
            user_id="user_a",
            kind="mail_alert",
            title="Email quan trọng: **Hợp đồng**",
            body=r"Từ \[Nam\]",
            dedup_key="m1",
            data={"action_label": "Đọc email", "suggested_query": "việc cần làm?"},
        )
        assert notification is not None
        notification.created_at = datetime.now(UTC) + timedelta(seconds=1)
        await session.commit()

    assert await bot.forward_notifications() == 1
    chat_id, text, buttons = client.sent[-1]
    assert chat_id == 100 and text == "🔔 Email quan trọng: Hợp đồng\n\nTừ [Nam]"
    assert buttons == [[{"text": "Đọc email", "callback_data": f"n:{notification.id}"}]]
    assert await bot.forward_notifications() == 0

    await bot.handle_update(_button(100, f"n:{notification.id}"))
    assert "Gọi anh Nam" in client.sent[-1][1]


def test_to_plain() -> None:
    assert to_plain("**Nguồn:** [W1] \\[x\\] — <https://a.vn> [t](https://b.vn)") == (
        "Nguồn: [W1] [x] — https://a.vn t (https://b.vn)"
    )


async def test_client_calls_and_errors() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 1}, "x"]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "result": True})

    client = TelegramClient(
        TelegramSettings(bot_token="123:abc"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await client.get_updates(5) == [{"update_id": 1}]
    assert seen[0][0] == "/bot123:abc/getUpdates" and seen[0][1]["offset"] == 5
    with pytest.raises(ExternalServiceError):
        await client.send_message(1, "x" * 5000)
    assert len(seen[1][1]["text"]) == 4000
    await client.answer_callback("cb", "ok")
    await client.clear_buttons(1, 2)
    with pytest.raises(ValueError):
        TelegramClient(TelegramSettings())


async def test_link_api(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        app = FastAPI()
        app.include_router(telegram_router)
        app.dependency_overrides[get_db_session] = lambda: session
        app.dependency_overrides[get_current_user_id] = lambda: "user_a"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            status = (await http.get("/telegram/status")).json()
            assert status["linked"] is False
            code = (await http.post("/telegram/link-code")).json()["code"]
            assert len(code) == 6 and code.isdigit()
            link = await TelegramLinkService.redeem(session, code, 100)
            assert link is not None
            assert (await http.get("/telegram/status")).json()["linked"] is True
            assert (await http.delete("/telegram/link")).json()["linked"] is False
            stored = await session.get(TelegramLink, link.id)
            assert stored is not None and stored.chat_id is None
