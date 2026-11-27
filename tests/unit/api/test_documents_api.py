"""Ownership checks of the generated-documents routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.api.routes.documents as documents_module
from app.api.dependencies import get_current_user_id
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import Conversation, User
from app.infrastructure.db.session import get_db_session


class _FakeService:
    """Records the conversation id the route passes on instead of drafting."""

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    async def create_outline(self, session: AsyncSession, **kwargs: Any) -> Any:
        """Stand in for outline generation; the route must not reach it on 404."""
        self.calls.append(kwargs.get("conversation_id"))
        raise RuntimeError("stop after the ownership check")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all([User(id="user_a", email="a@x.vn"), User(id="user_b", email="b@x.vn")])
        await session.flush()
        session.add_all(
            [
                Conversation(id="conv_a", user_id="user_a", title="A"),
                Conversation(id="conv_b", user_id="user_b", title="B"),
            ]
        )
        await session.flush()
        yield session
    await engine.dispose()


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> _FakeService:
    service = _FakeService()
    monkeypatch.setattr(documents_module, "_service", lambda: service)
    return service


@pytest.fixture
def client_app(db_session: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.include_router(documents_module.router)
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_user_id] = lambda: "user_a"
    return app


@pytest.mark.parametrize("conversation_id", ["conv_b", "missing"])
async def test_outline_rejects_a_conversation_the_caller_does_not_own(
    client_app: FastAPI, fake_service: _FakeService, conversation_id: str
) -> None:
    async with AsyncClient(transport=ASGITransport(app=client_app), base_url="http://t") as client:
        response = await client.post(
            "/documents/outline",
            json={"request": "Viết báo cáo", "conversation_id": conversation_id},
        )
    assert response.status_code == 404
    assert response.json()["detail"] == documents_module.CONVERSATION_NOT_FOUND
    assert fake_service.calls == []


async def test_outline_passes_on_the_callers_own_conversation(
    client_app: FastAPI, fake_service: _FakeService
) -> None:
    async with AsyncClient(transport=ASGITransport(app=client_app), base_url="http://t") as client:
        with pytest.raises(RuntimeError, match="ownership check"):
            await client.post(
                "/documents/outline",
                json={"request": "Viết báo cáo", "conversation_id": "conv_a"},
            )
    assert fake_service.calls == ["conv_a"]


async def test_unknown_document_answers_vietnamese_404(client_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=client_app), base_url="http://t") as client:
        response = await client.get("/documents/nope")
    assert response.status_code == 404
    assert response.json()["detail"] == documents_module.DOCUMENT_NOT_FOUND
