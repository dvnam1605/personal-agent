"""Server-side evidence: snapshot persistence, citation rebuild, owner-only access."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.conversations import SaveTurnRequest, save_turn
from app.api.routes.runs import get_run_evidence
from app.domain.models.retrieval import Evidence, EvidenceBundle
from app.infrastructure.db.base import Base
from app.infrastructure.db.evidence_repository import record_run_evidence, run_evidence
from app.infrastructure.db.models import AssistantRun, Conversation, Message, User
from app.services.retrieval.answer.citations import CitationNumberer, evidence_snapshot

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def _bundle() -> EvidenceBundle:
    items = [
        Evidence(
            evidence_id=uuid.uuid4().hex,
            kind="CHILD",
            content_raw=f"nội dung {i}",
            token_estimate=5,
            primary_chunk_id=f"chunk-{i}",
            document_id=f"doc-{i}",
            document_version_id=f"doc-{i}",
            anchors={"version_number": 2, "profile_title": f"Tài liệu {i}"},
        )
        for i in range(3)
    ]
    return EvidenceBundle(
        items=items,
        total_tokens=15,
        documents_used=[i.document_id for i in items],
        parent_ids_used=[],
        retrieval_trace_id="trace-1",
    )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([User(id=OWNER, email="o@x.local"), User(id=OTHER, email="p@x.local")])
        s.add(Conversation(id="conv-1", user_id=OWNER, title="t"))
        for run_id, uid in (("run-1", OWNER), ("run-2", OTHER)):
            s.add(
                AssistantRun(
                    id=run_id,
                    user_id=uid,
                    correlation_id=run_id,
                    request="q",
                    route_type="DIRECT_SPECIALIST",
                    complexity="DIRECT",
                )
            )
        await s.commit()
        yield s
    await engine.dispose()


async def _record(session: AsyncSession, run_id: str) -> EvidenceBundle:
    bundle = _bundle()
    numberer = CitationNumberer(bundle)
    numberer.rewrite("Ý [E3]. Ý [E1].")
    await record_run_evidence(
        session,
        run_id,
        evidence=evidence_snapshot(bundle, numberer),
        retrieval_trace_id=bundle.retrieval_trace_id,
        answer_status="SUFFICIENT",
    )
    await session.commit()
    return bundle


async def test_snapshot_records_every_unit_and_marks_cited(session: AsyncSession) -> None:
    bundle = await _record(session, "run-1")
    rows = await run_evidence(session, "run-1")
    assert [r.label for r in rows] == ["E1", "E2", "E3"]
    assert 'id="E1"' in rows[0].prompt_text and "nội dung 0" in rows[0].prompt_text
    cited = await run_evidence(session, "run-1", cited_only=True)
    assert [(r.evidence_id, r.citation_number) for r in cited] == [
        (bundle.items[2].evidence_id, 1),
        (bundle.items[0].evidence_id, 2),
    ]
    run = await session.get(AssistantRun, "run-1")
    assert run is not None
    assert (run.retrieval_trace_id, run.answer_status) == ("trace-1", "SUFFICIENT")


async def test_turn_rebuilds_citations_from_server_evidence(session: AsyncSession) -> None:
    await _record(session, "run-1")
    forged = {"data": {"citations": [{"evidence_id": "forged", "text": "bịa"}]}}
    body = SaveTurnRequest(
        user_query="q", assistant_response="a [1][2]", run_id="run-1", metadata=forged
    )
    await save_turn("conv-1", body, user_id=OWNER, session=session)
    msg = next(
        m
        for m in (await session.execute(Message.__table__.select())).mappings()
        if m["role"] == "assistant"
    )
    citations = msg["metadata"]["data"]["citations"]
    assert [c["citation_number"] for c in citations] == [1, 2]
    assert all(c["evidence_id"] != "forged" for c in citations)
    assert msg["metadata"]["data"]["sufficiency"] == "SUFFICIENT"


async def test_turn_strips_citations_for_someone_elses_run(session: AsyncSession) -> None:
    await _record(session, "run-2")
    body = SaveTurnRequest(
        user_query="q",
        assistant_response="a",
        run_id="run-2",
        metadata={"data": {"citations": [{"evidence_id": "x"}]}},
    )
    await save_turn("conv-1", body, user_id=OWNER, session=session)
    msg = next(
        m
        for m in (await session.execute(Message.__table__.select())).mappings()
        if m["role"] == "assistant"
    )
    assert "citations" not in msg["metadata"].get("data", {})


async def test_evidence_endpoint_is_owner_only(session: AsyncSession) -> None:
    await _record(session, "run-1")
    body = await get_run_evidence("run-1", user_id=OWNER, session=session)
    assert len(body["evidence"]) == 3 and body["answer_status"] == "SUFFICIENT"
    with pytest.raises(HTTPException) as exc:
        await get_run_evidence("run-1", user_id=OTHER, session=session)
    assert exc.value.status_code == 404
