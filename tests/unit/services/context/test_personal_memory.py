"""Unit tests for long-term personal memory: commands, SQL store and the model context."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.enums import MemoryType
from app.domain.models.context.memory import MemoryItem
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.services.context.personal_memory import (
    MemoryCommand,
    PersonalMemoryService,
    parse_memory_command,
    recall_message,
)
from app.services.context.sql_memory_store import SqlMemoryStore


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all([User(id="user_a", email="a@x.vn"), User(id="user_b", email="b@x.vn")])
        await session.flush()
        yield session
    await engine.dispose()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "Nhớ rằng chữ ký của tôi là Nam - ATIN",
            MemoryCommand("remember", "chữ ký của tôi là Nam - ATIN"),
        ),
        ("ghi nho: anh Nam la nam@atin.vn", MemoryCommand("remember", "anh Nam la nam@atin.vn")),
        (
            "Hãy ghi nhớ rằng tôi thích email ngắn.",
            MemoryCommand("remember", "tôi thích email ngắn"),
        ),
        ("quên đi chữ ký cũ", MemoryCommand("forget", "chữ ký cũ")),
        ("Hãy quên: số điện thoại cũ", MemoryCommand("forget", "số điện thoại cũ")),
        ("Bạn nhớ gì về tôi?", MemoryCommand("recall")),
        ("Nhớ lại cuộc họp hôm qua", None),
        ("quen thuộc với dự án X", None),
        ("Tôi quên mật khẩu", None),
        ("Lịch ngày mai?", None),
    ],
)
def test_parse_memory_command(query: str, expected: MemoryCommand | None) -> None:
    assert parse_memory_command(query) == expected


async def test_remember_classifies_and_supersedes_preferences(db_session: AsyncSession) -> None:
    memory = PersonalMemoryService(SqlMemoryStore(db_session))

    fact = await memory.remember("user_a", "anh Nam là nam@atin.vn")
    first = await memory.remember("user_a", "chữ ký: Nam - ATIN")
    second = await memory.remember("user_a", "chữ ký: Đậu Văn Nam, ATIN")
    tone = await memory.remember("user_a", "tôi thích email ngắn gọn")

    assert fact.memory_type is MemoryType.FACT
    assert first.memory_type is second.memory_type is tone.memory_type is MemoryType.PREFERENCE
    active = {item.content for item in await memory.active("user_a")}
    assert active == {
        "anh Nam là nam@atin.vn",
        "chữ ký: Đậu Văn Nam, ATIN",
        "tôi thích email ngắn gọn",
    }
    assert await memory.active("user_b") == []


async def test_forget_is_specific_and_refuses_vague_requests(db_session: AsyncSession) -> None:
    memory = PersonalMemoryService(SqlMemoryStore(db_session))
    for text in [
        "dự án A họp thứ 2",
        "dự án A dùng FastAPI",
        "dự án A deadline 1/12",
        "dự án A ở Hà Nội",
    ]:
        await memory.remember("user_a", text)

    vague = await memory.forget("user_a", "dự án A")
    assert vague.forgotten == [] and len(vague.ambiguous) == 4
    assert (await memory.forget("user_a", "chuyện đó")).forgotten == []

    exact = await memory.forget("user_a", "dự án A deadline")
    assert [item.content for item in exact.forgotten] == ["dự án A deadline 1/12"]
    assert len(await memory.active("user_a")) == 3
    assert (await memory.forget("user_b", "dự án A FastAPI")).forgotten == []


async def test_context_lists_preferences_and_relevant_facts(db_session: AsyncSession) -> None:
    memory = PersonalMemoryService(SqlMemoryStore(db_session))
    assert await memory.context_for("user_a", "soạn email") == ""

    await memory.remember("user_a", "tôi thích email ngắn gọn")
    await memory.remember("user_a", "anh Nam là nam@atin.vn")
    await memory.remember("user_a", "dự án B dùng React")

    context = await memory.context_for("user_a", "Soạn email gửi anh Nam")
    assert context.startswith("Thông tin người dùng đã nhờ trợ lý ghi nhớ")
    assert "- tôi thích email ngắn gọn" in context
    assert "- anh Nam là nam@atin.vn" in context
    assert "React" not in context


async def test_store_round_trip_stale_and_delete(db_session: AsyncSession) -> None:
    store = SqlMemoryStore(db_session)
    item = await store.save_memory(
        MemoryItem(user_id="user_a", memory_type=MemoryType.FACT, content="x", metadata={"k": 1})
    )
    loaded = await store.get_memory(item.id)
    assert loaded is not None and loaded.metadata == {"k": 1}

    await store.save_memory(item.model_copy(update={"content": "y"}))
    assert (await store.get_memory(item.id)).content == "y"  # type: ignore[union-attr]

    assert await store.mark_stale(item.id)
    assert await store.list_memories("user_a") == []
    assert len(await store.list_memories("user_a", include_stale=True)) == 1
    assert await store.delete_memory(item.id)
    assert not await store.delete_memory(item.id)
    assert not await store.mark_stale("missing")
    assert await store.get_memory("missing") is None


def test_recall_message() -> None:
    assert "chưa ghi nhớ" in recall_message([])
    item = MemoryItem(user_id="u", memory_type=MemoryType.FACT, content="anh Nam là nam@x.vn")
    assert "- anh Nam là nam@x.vn" in recall_message([item])
