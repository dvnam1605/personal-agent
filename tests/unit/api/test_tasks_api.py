"""Unit tests for the to-do workspace endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user_id
from app.api.routes.approvals import get_approval_execution_service
from app.api.routes.approvals import router as approvals_router
from app.api.routes.query import get_query_orchestrator
from app.api.routes.tasks import router as tasks_router
from app.domain.errors import AppError
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_db_session
from app.integrations.google_tasks import TaskItem
from app.services.approvals.execution import ApprovalExecutionService
from app.services.routing.query import QueryOrchestrator
from app.tools.google_tasks import GoogleTasksTools


class FakeTasks:
    def __init__(self) -> None:
        self.created: list[tuple[str, date | None]] = []
        self.completed: list[str] = []

    async def list_tasks(self, **_kwargs: Any) -> list[TaskItem]:
        return [
            TaskItem(id="t1", title="Gọi anh Nam", due=date(2026, 10, 20)),
            TaskItem(id="t2", title="Đã xong", status="completed"),
        ]

    async def insert_task(
        self, title: str, *, notes: str | None = None, due: date | None = None
    ) -> TaskItem:
        self.created.append((title, due))
        return TaskItem(id="new", title=title, due=due)

    async def complete_task(self, task_id: str) -> TaskItem:
        self.completed.append(task_id)
        return TaskItem(id=task_id, title="Gọi anh Nam", status="completed")


async def _app_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


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


async def test_list_propose_and_approve(db_session: AsyncSession) -> None:
    fake = FakeTasks()

    async def tasks_for_user(*_args: Any) -> FakeTasks:
        return fake

    app = FastAPI()
    app.include_router(tasks_router)
    app.include_router(approvals_router)
    app.add_exception_handler(AppError, _app_error)
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_user_id] = lambda: "user_a"
    app.dependency_overrides[get_query_orchestrator] = lambda: QueryOrchestrator(
        tasks_for_user=tasks_for_user
    )
    app.dependency_overrides[get_approval_execution_service] = lambda: ApprovalExecutionService(
        tasks_execute=GoogleTasksTools(fake).execute  # type: ignore[arg-type]
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        listed = (await client.get("/tasks")).json()
        assert [item["id"] for item in listed] == ["t1"]

        created = await client.post(
            "/tasks/propose",
            json={"action": "create", "title": "  Gửi   hợp đồng ", "due": "2026-10-30"},
        )
        body = created.json()
        assert body["description"] == "Thêm việc «Gửi hợp đồng» (hạn 30/10/2026) vào Google Tasks."
        assert fake.created == []
        approved = await client.post(
            f"/approvals/{body['approval_id']}/approve", json={"execute": True}
        )
        assert approved.json()["executed"] is True
        assert fake.created == [("Gửi hợp đồng", date(2026, 10, 30))]

        done = (
            await client.post("/tasks/propose", json={"action": "complete", "task_id": "t1"})
        ).json()
        assert done["description"] == "Đánh dấu xong việc «Gọi anh Nam» trên Google Tasks."
        await client.post(f"/approvals/{done['approval_id']}/approve", json={"execute": True})
        assert fake.completed == ["t1"]

        assert (
            await client.post("/tasks/propose", json={"action": "complete", "task_id": "t2"})
        ).status_code == 404
        assert (
            await client.post("/tasks/propose", json={"action": "create", "title": " "})
        ).status_code == 422
        assert (await client.post("/tasks/propose", json={"action": "complete"})).status_code == 422
