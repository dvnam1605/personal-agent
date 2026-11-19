"""Unit tests for the notification centre endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user_id
from app.api.routes.notifications import router as notifications_router
from app.domain.errors import AppError
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_db_session
from app.services.proactive import NotificationService


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
    app.include_router(notifications_router)
    app.add_exception_handler(AppError, _app_error)
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_user_id] = lambda: "user_a"
    return app


async def _seed(session: AsyncSession) -> tuple[str, str]:
    mine = await NotificationService.create(
        session, user_id="user_a", kind="morning_briefing", title="Bản tin", body="b", dedup_key="1"
    )
    await NotificationService.create(
        session, user_id="user_a", kind="meeting_prep", title="Họp", body="b", dedup_key="2"
    )
    theirs = await NotificationService.create(
        session, user_id="user_b", kind="meeting_prep", title="Khác", body="b", dedup_key="3"
    )
    assert mine is not None and theirs is not None
    await session.flush()
    return mine.id, theirs.id


async def test_list_mark_read_and_read_all(client_app: FastAPI, db_session: AsyncSession) -> None:
    mine, theirs = await _seed(db_session)
    async with AsyncClient(transport=ASGITransport(app=client_app), base_url="http://t") as client:
        listed = (await client.get("/notifications")).json()
        assert listed["unread_count"] == 2
        assert {item["title"] for item in listed["items"]} == {"Bản tin", "Họp"}

        assert (await client.post(f"/notifications/{theirs}/read")).status_code == 404
        assert (await client.post(f"/notifications/{mine}/read")).json() == {"updated": 1}
        unread = (await client.get("/notifications", params={"unread_only": True})).json()
        assert [item["title"] for item in unread["items"]] == ["Họp"]

        assert (await client.post("/notifications/read-all")).json() == {"updated": 1}
        assert (await client.get("/notifications")).json()["unread_count"] == 0

        assert (await client.get("/notifications", params={"limit": 0})).status_code == 422
