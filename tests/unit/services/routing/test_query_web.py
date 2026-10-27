"""Unit tests for explicit web search in the query orchestrator."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.domain.errors import ExternalServiceError
from app.domain.models.retrieval.web import WebSearchPage, WebSearchResultItem
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.services.routing.query import QueryOrchestrator
from app.services.web import WebAnswerService
from app.services.web import answer as answer_module
from app.tools.knowledge import MockWebSearchProvider


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


@pytest.fixture
def web_service(monkeypatch: pytest.MonkeyPatch) -> WebAnswerService:
    async def fake_llm(*_args: Any, **_kwargs: Any) -> str:
        return "Giá vàng hôm nay tăng [W1]."

    monkeypatch.setattr(answer_module, "llm_text", fake_llm)
    page = WebSearchPage(
        items=[WebSearchResultItem(url="https://a.vn/vang", title="Giá vàng", snippet="tăng")]
    )
    return WebAnswerService(MockWebSearchProvider(default=page))


async def test_explicit_web_request_is_answered_with_sources(
    db_session: AsyncSession, web_service: WebAnswerService
) -> None:
    orchestrator = QueryOrchestrator(web_answer_service=web_service)
    result = await orchestrator.handle(db_session, "user_a", "Tìm trên web về giá vàng hôm nay")

    assert result.status == "completed"
    assert result.message.startswith("Giá vàng hôm nay tăng [W1].")
    assert "<https://a.vn/vang>" in result.message
    assert result.data["web_query"] == "giá vàng hôm nay"
    assert result.data["web_sources"][0]["url"] == "https://a.vn/vang"


async def test_web_is_unavailable_without_a_key(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.web_search, "api_key", None)
    result = await QueryOrchestrator().handle(db_session, "user_a", "google: FastAPI SSE")
    assert result.status == "blocked"
    assert "WEB_SEARCH__API_KEY" in result.message


async def test_provider_failure_is_reported(db_session: AsyncSession) -> None:
    class Broken:
        async def answer(self, question: str) -> Any:
            raise ExternalServiceError("down")

    result = await QueryOrchestrator(web_answer_service=Broken()).handle(
        db_session, "user_a", "tra cứu trên mạng: luật đất đai"
    )
    assert result.status == "blocked"


async def test_stream_path_answers_web_requests(
    db_session: AsyncSession, web_service: WebAnswerService
) -> None:
    orchestrator = QueryOrchestrator(web_answer_service=web_service)
    chunks = [
        chunk
        async for chunk in orchestrator.handle_stream(db_session, "user_a", "Tin tức về giá vàng")
    ]
    text = "".join(
        json.loads(chunk.split("data: ", 1)[1])["delta"]
        for chunk in chunks
        if chunk.startswith("event: token")
    )
    assert text.startswith("Giá vàng hôm nay tăng")
    assert '"status": "completed"' in chunks[-1]
