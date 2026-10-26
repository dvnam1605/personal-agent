"""Conversation history and follow-up scope come only from the caller's own conversations.

``conversation_id`` is client-supplied on /query, so another user's id must yield no
history and no retrieval scope.
"""

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.enums import Complexity, Domain, RouteType
from app.domain.models.routing.route import RouteDecision
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import AssistantRun, Conversation, Message, RunEvidence, User
from app.services.routing.query.followup import _FOLLOWUP_SCOPE, FollowupMixin
from app.services.routing.query.orchestrator import QueryOrchestrator

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([User(id=OWNER, email="owner@x.local"), User(id=OTHER, email="other@x.local")])
        s.add(Conversation(id="conv-owner", user_id=OWNER, title="t"))
        s.add(Conversation(id="conv-other", user_id=OTHER, title="t"))
        s.add(
            AssistantRun(
                id="run-owner",
                user_id=OWNER,
                correlation_id="run-owner",
                request="q",
                route_type="DIRECT_SPECIALIST",
                complexity="DIRECT",
            )
        )
        await s.flush()
        s.add_all(
            [
                Message(
                    conversation_id="conv-owner", role="user", content="CANARY-QUESTION " * 400
                ),
                Message(
                    conversation_id="conv-owner",
                    role="assistant",
                    content="CANARY-ANSWER",
                    metadata_={"data": {"citations": [{"document_id": "doc-owner"}]}},
                ),
                # A message in the other user's conversation pointing at the owner's run.
                Message(
                    conversation_id="conv-other", role="assistant", content="x", run_id="run-owner"
                ),
                RunEvidence(
                    run_id="run-owner",
                    label="E1",
                    rank=1,
                    evidence_id="ev-1",
                    document_id="doc-owner-run",
                    prompt_text="p",
                    content_hash="h",
                    cited=True,
                ),
            ]
        )
        await s.commit()
        yield s
    await engine.dispose()


async def test_history_of_another_users_conversation_is_not_loaded(session: AsyncSession) -> None:
    assert await FollowupMixin._load_conversation_history(session, "conv-owner", OTHER) == []

    own = await FollowupMixin._load_conversation_history(session, "conv-owner", OWNER)
    assert [m["role"] for m in own] == ["user", "assistant"]
    assert len(own[0]["content"]) == 4000


async def test_followup_scope_ignores_another_users_conversation(session: AsyncSession) -> None:
    await FollowupMixin._set_followup_scope(session, "conv-owner", OTHER, "anchor")
    assert _FOLLOWUP_SCOPE.get() is None

    await FollowupMixin._set_followup_scope(session, "conv-owner", OWNER, "anchor")
    assert _FOLLOWUP_SCOPE.get() == (["doc-owner"], "anchor")


async def test_followup_scope_does_not_follow_a_message_into_a_foreign_run(
    session: AsyncSession,
) -> None:
    await FollowupMixin._set_followup_scope(session, "conv-other", OTHER, "anchor")
    assert _FOLLOWUP_SCOPE.get() is None


async def test_triage_runs_off_the_event_loop() -> None:
    """A slow, blocking triage call must not stall other coroutines on the loop."""

    class SlowTriage:
        def triage(self, query: str) -> RouteDecision:
            time.sleep(0.3)
            return RouteDecision(
                route_type=RouteType.DIRECT_SPECIALIST,
                target_agent="KnowledgeResearchAgent",
                confidence=0.9,
                reasoning="stub",
                domains=[Domain.KNOWLEDGE_RESEARCH],
                complexity=Complexity.DIRECT,
                reason_code="STUB",
            )

    orchestrator = QueryOrchestrator(triage=SlowTriage())  # type: ignore[arg-type]
    gaps: list[float] = []

    async def heartbeat() -> None:
        last = time.perf_counter()
        for _ in range(20):
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    await asyncio.gather(orchestrator._triage_in_context("abc xyz", None), heartbeat())
    assert max(gaps) < 0.2
