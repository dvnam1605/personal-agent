"""Unit tests for the memory page endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user_id
from app.api.routes.memories import router as memories_router
from app.domain.errors import AppError
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_db_session
from app.services.context.personal_memory import PersonalMemoryService
from app.services.context.sql_memory_store import SqlMemoryStore


async def _app_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


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


@pytest.fixture
def client_app(db_session: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.include_router(memories_router)
    app.add_exception_handler(AppError, _app_error)
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_user_id] = lambda: "user_a"
    return app


async def test_create_list_update_and_delete(client_app: FastAPI, db_session: AsyncSession) -> None:
    theirs = await PersonalMemoryService(SqlMemoryStore(db_session)).remember("user_b", "bí mật")
    async with AsyncClient(transport=ASGITransport(app=client_app), base_url="http://t") as client:
        created = await client.post("/memories", json={"content": "  chữ ký: Nam - ATIN  "})
        assert created.status_code == 201
        body = created.json()
        assert body == {
            "id": body["id"],
            "memory_type": "preference",
            "content": "chữ ký: Nam - ATIN",
            "category": "signature",
        }
        fact = (await client.post("/memories", json={"content": "anh Nam là nam@x.vn"})).json()
        assert fact["memory_type"] == "fact" and fact["category"] is None

        listed = (await client.get("/memories")).json()
        assert {item["content"] for item in listed} == {"chữ ký: Nam - ATIN", "anh Nam là nam@x.vn"}

        updated = await client.put(
            f"/memories/{fact['id']}", json={"content": "anh Nam: nam@atin.vn"}
        )
        assert updated.json()["content"] == "anh Nam: nam@atin.vn"

        assert (
            await client.put(f"/memories/{theirs.id}", json={"content": "x"})
        ).status_code == 404
        assert (await client.delete(f"/memories/{theirs.id}")).status_code == 404
        assert (await client.post("/memories", json={"content": "   "})).status_code == 422

        assert (await client.delete(f"/memories/{fact['id']}")).status_code == 204
        assert (await client.delete(f"/memories/{fact['id']}")).status_code == 404
        assert [item["content"] for item in (await client.get("/memories")).json()] == [
            "chữ ký: Nam - ATIN"
        ]
