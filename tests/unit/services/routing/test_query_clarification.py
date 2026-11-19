"""Unit tests for pausing a request to ask the user and resuming it with the answer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.db.base import Base
from app.infrastructure.db.models import Conversation, User
from app.services.critic import Critique, EmailDraft
from app.services.routing.query import QueryOrchestrator
from app.services.routing.query import specialists as specialists_module
from app.services.routing.query.clarification import PENDING_KEY
from app.services.routing.query.drafting import DraftPlan, prepare_draft, review_note
from tests.unit.services.routing._critics import AcceptingCritic

DRAFT_QUERY = "Soạn email xin nghỉ phép ngày mai"


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all(
            [
                User(id="user_a", email="a@x.vn"),
                User(id="user_b", email="b@x.vn"),
                Conversation(id="conv_a", user_id="user_a"),
                Conversation(id="conv_b", user_id="user_b"),
            ]
        )
        await session.flush()
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def model_free_draft(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The composer returns a draft with a made-up recipient, as a model might."""
    requests: list[str] = []

    async def fake_compose(query: str, **_kwargs: Any) -> tuple[str, str, list[str]]:
        requests.append(query)
        return (
            "Xin nghỉ phép",
            "Kính gửi anh,\n\nEm xin nghỉ ngày mai.\n\n[Tên của bạn]",
            ["quanly@doan.vn"],
        )

    monkeypatch.setattr(specialists_module, "compose_email_draft", fake_compose)
    return requests


async def _ask(
    orchestrator: QueryOrchestrator, session: AsyncSession, query: str, conv: str = "conv_a"
) -> Any:
    return await orchestrator.handle(session, "user_a", query, conversation_id=conv)


async def test_unknown_recipient_pauses_then_the_answer_resumes_the_draft(
    db_session: AsyncSession, model_free_draft: list[str]
) -> None:
    orchestrator = QueryOrchestrator(draft_critic=AcceptingCritic())

    asked = await _ask(orchestrator, db_session, DRAFT_QUERY)
    assert asked.status == "clarification_needed"
    assert asked.approval_id is None
    conversation = await db_session.get(Conversation, "conv_a")
    assert conversation is not None and conversation.metadata_[PENDING_KEY]["query"] == DRAFT_QUERY

    again = await _ask(orchestrator, db_session, "gửi anh Nam nhé")
    assert "Tôi cần địa chỉ email" in again.message
    assert PENDING_KEY in conversation.metadata_

    done = await _ask(orchestrator, db_session, "nam@atin.vn, lan@atin.vn")
    assert done.status == "needs_approval"
    assert done.data["to"] == ["nam@atin.vn", "lan@atin.vn"]
    assert model_free_draft[-1] == f"{DRAFT_QUERY} (gửi tới nam@atin.vn, lan@atin.vn)"
    assert PENDING_KEY not in conversation.metadata_


async def test_cancel_expiry_and_other_owners(db_session: AsyncSession) -> None:
    orchestrator = QueryOrchestrator(draft_critic=AcceptingCritic())
    await _ask(orchestrator, db_session, DRAFT_QUERY)
    cancelled = await _ask(orchestrator, db_session, "Hủy")
    assert cancelled.message == "Đã hủy yêu cầu đang chờ."

    await _ask(orchestrator, db_session, DRAFT_QUERY)
    conversation = await db_session.get(Conversation, "conv_a")
    assert conversation is not None
    pending = dict(conversation.metadata_[PENDING_KEY])
    pending["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    conversation.metadata_ = {PENDING_KEY: pending}
    expired = await _ask(orchestrator, db_session, "nam@atin.vn")
    assert expired.status != "needs_approval"
    assert PENDING_KEY not in conversation.metadata_

    # Another user's conversation id is never read or written.
    foreign = await _ask(orchestrator, db_session, DRAFT_QUERY, conv="conv_b")
    assert foreign.status == "clarification_needed"
    other = await db_session.get(Conversation, "conv_b")
    assert other is not None and PENDING_KEY not in (other.metadata_ or {})


async def test_stream_path_asks_and_resumes(db_session: AsyncSession) -> None:
    orchestrator = QueryOrchestrator(draft_critic=AcceptingCritic())

    async def stream(query: str) -> str:
        chunks = [
            chunk
            async for chunk in orchestrator.handle_stream(
                db_session, "user_a", query, conversation_id="conv_a"
            )
        ]
        return chunks[-1]

    assert '"status": "clarification_needed"' in await stream(DRAFT_QUERY)
    assert '"status": "needs_approval"' in await stream("nam@atin.vn")


async def test_prepare_draft_fills_signature_and_reports_review() -> None:
    plan = await prepare_draft(
        "xin nghỉ gửi nam@atin.vn",
        "Xin nghỉ",
        "Kính gửi anh,\n\nEm xin nghỉ ngày mai vì việc nhà.\n\n[Tên của bạn]",
        ["nam@atin.vn", "boss@made.up"],
        signature="Đậu Văn Nam",
        critic=AcceptingCritic(),  # type: ignore[arg-type]
    )
    assert plan.draft.recipients == ("nam@atin.vn",)
    assert plan.draft.body.endswith("Đậu Văn Nam")

    draft = EmailDraft(subject="s", body="b", recipients=("a@b.vn",))
    assert review_note(DraftPlan(draft=draft)) == ""
    assert review_note(DraftPlan(draft=draft, fixed=["Thiếu ký tên."])).startswith("Đã tự rà soát")
    assert (
        review_note(DraftPlan(draft=draft, remaining=["Sai ngày."])) == "Lưu ý khi duyệt: Sai ngày."
    )
    assert Critique(ok=True).issues == []
