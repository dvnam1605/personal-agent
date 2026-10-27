"""Unit tests for to-do commands in the orchestrator, through approval and execution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user_id
from app.api.routes.approvals import get_approval_execution_service
from app.api.routes.approvals import router as approvals_router
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_db_session
from app.integrations.google_tasks import TaskItem
from app.services.approvals.execution import ApprovalExecutionService
from app.services.routing.query import QueryOrchestrator
from app.tools.google_tasks import GoogleTasksTools

NOW = datetime(2026, 10, 27, 2, 0, tzinfo=UTC)  # 09:00 Tuesday in Hanoi


class FakeTasks:
    def __init__(self) -> None:
        self.items = [
            TaskItem(id="t1", title="Gọi anh Nam", due=date(2026, 10, 25)),
            TaskItem(id="t2", title="Nộp báo cáo quý", due=date(2026, 10, 27)),
            TaskItem(id="t3", title="Đọc tài liệu"),
            TaskItem(id="t4", title="Gọi chị Lan"),
        ]
        self.created: list[dict[str, Any]] = []
        self.completed: list[str] = []

    async def list_tasks(self, **_kwargs: Any) -> list[TaskItem]:
        return list(self.items)

    async def insert_task(
        self, title: str, *, notes: str | None = None, due: date | None = None
    ) -> TaskItem:
        self.created.append({"title": title, "due": due})
        return TaskItem(id="new", title=title, due=due)

    async def complete_task(self, task_id: str) -> TaskItem:
        self.completed.append(task_id)
        return TaskItem(id=task_id, title="x", status="completed")


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


def _orchestrator(fake: FakeTasks) -> QueryOrchestrator:
    async def tasks_for_user(_session: AsyncSession, _user_id: str) -> FakeTasks:
        return fake

    return QueryOrchestrator(tasks_for_user=tasks_for_user, clock=lambda: NOW)


async def test_list_orders_by_due_and_flags_overdue(db_session: AsyncSession) -> None:
    result = await _orchestrator(FakeTasks()).handle(db_session, "user_a", "việc cần làm hôm nay?")
    assert result.status == "completed"
    lines = result.message.splitlines()
    assert lines[0] == "Bạn còn 4 việc:"
    assert lines[1] == "- Gọi anh Nam — **quá hạn** (25/10)"
    assert lines[2] == "- Nộp báo cáo quý — hạn hôm nay"


async def test_create_needs_approval_then_executes_with_the_token(db_session: AsyncSession) -> None:
    fake = FakeTasks()
    proposed = await _orchestrator(fake).handle(
        db_session, "user_a", "Thêm việc: gửi hợp đồng hạn thứ 6"
    )
    assert proposed.status == "needs_approval"
    assert proposed.data["task"] == {"title": "gửi hợp đồng", "due": "2026-10-30"}
    assert fake.created == []

    app = FastAPI()
    app.include_router(approvals_router)
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_user_id] = lambda: "user_a"
    app.dependency_overrides[get_approval_execution_service] = lambda: ApprovalExecutionService(
        tasks_execute=GoogleTasksTools(fake).execute  # type: ignore[arg-type]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post(
            f"/approvals/{proposed.approval_id}/approve", json={"execute": True}
        )
    body = response.json()
    assert body["executed"] is True, body
    assert fake.created == [{"title": "gửi hợp đồng", "due": date(2026, 10, 30)}]


async def test_complete_requires_a_single_match(db_session: AsyncSession) -> None:
    fake = FakeTasks()
    orchestrator = _orchestrator(fake)

    ambiguous = await orchestrator.handle(db_session, "user_a", "xong việc gọi")
    assert ambiguous.status == "completed" and "Có nhiều việc khớp" in ambiguous.message

    missing = await orchestrator.handle(db_session, "user_a", "xong việc mua vé")
    assert "Không thấy việc nào khớp" in missing.message

    single = await orchestrator.handle(db_session, "user_a", "Xong việc gọi anh Nam")
    assert single.status == "needs_approval"
    assert single.data["proposal"]["parameters"] == {"task_id": "t1", "title": "Gọi anh Nam"}
    assert fake.completed == []


async def test_stream_path_offers_the_approval(db_session: AsyncSession) -> None:
    chunks = [
        chunk
        async for chunk in _orchestrator(FakeTasks()).handle_stream(
            db_session, "user_a", "nhắc tôi nộp báo cáo trước 15/11"
        )
    ]
    assert any(chunk.startswith("event: citations") and "approval_id" in chunk for chunk in chunks)
    assert '"status": "needs_approval"' in chunks[-1]
