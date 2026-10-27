"""Unit tests for memory commands and personal context in the query orchestrator."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.db.base import Base
from app.infrastructure.db.models import AssistantRun, Memory, User
from app.services.routing.query import QueryOrchestrator
from app.services.routing.query import specialists as specialists_module
from tests.unit.services.routing._critics import AcceptingCritic


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(User(id="user_a", email="a@x.vn"))
        await session.flush()
        yield session
    await engine.dispose()


async def test_remember_recall_and_forget_in_chat(db_session: AsyncSession) -> None:
    orchestrator = QueryOrchestrator(new_run_id=iter(["r1", "r2", "r3", "r4"]).__next__)

    remembered = await orchestrator.handle(db_session, "user_a", "Nhớ rằng anh Nam là nam@atin.vn")
    assert remembered.status == "completed"
    assert remembered.message == "Đã ghi nhớ: anh Nam là nam@atin.vn"
    assert remembered.data["memory_command"] == "remember"
    assert (await db_session.get(Memory, remembered.data["memory_id"])) is not None

    recalled = await orchestrator.handle(db_session, "user_a", "Bạn nhớ gì về tôi?")
    assert "- anh Nam là nam@atin.vn" in recalled.message

    forgotten = await orchestrator.handle(db_session, "user_a", "Hãy quên anh Nam")
    assert forgotten.message.startswith("Đã quên:")
    missing = await orchestrator.handle(db_session, "user_a", "quên đi chị Lan")
    assert "không có ghi nhớ nào" in missing.message

    run = await db_session.get(AssistantRun, "r1")
    assert run is not None and run.status == "completed"


async def test_email_draft_receives_personal_context(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_compose(query: str, **kwargs: Any) -> tuple[str, str, list[str]]:
        captured.update(kwargs)
        return "Tiêu đề", "Nội dung", ["nam@atin.vn"]

    monkeypatch.setattr(specialists_module, "compose_email_draft", fake_compose)
    critic = AcceptingCritic()
    orchestrator = QueryOrchestrator(draft_critic=critic)
    await orchestrator.handle(db_session, "user_a", "Ghi nhớ: chữ ký của tôi là Nam - ATIN")
    await orchestrator.handle(db_session, "user_a", "Ghi nhớ: anh Nam là nam@atin.vn")
    result = await orchestrator.handle(db_session, "user_a", "Soạn email gửi anh Nam xin nghỉ phép")

    assert result.status == "needs_approval"
    assert "chữ ký của tôi là Nam - ATIN" in captured["personal"]
    assert result.data["to"] == ["nam@atin.vn"]
    assert critic.reviewed[0].recipients == ("nam@atin.vn",)


async def test_stream_path_handles_memory_commands(db_session: AsyncSession) -> None:
    orchestrator = QueryOrchestrator()
    chunks = [
        chunk
        async for chunk in orchestrator.handle_stream(
            db_session, "user_a", "ghi nhớ: tôi thích email ngắn gọn"
        )
    ]
    deltas = [
        json.loads(chunk.split("data: ", 1)[1])["delta"]
        for chunk in chunks
        if chunk.startswith("event: token")
    ]
    assert "".join(deltas).startswith("Đã ghi nhớ")
    assert chunks[-1].startswith("event: done")
