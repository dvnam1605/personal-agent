"""Metadata route: question planning, parameterised SQL over profiles, rendering."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.text import unaccent_vietnamese
from app.domain.models.retrieval.metadata_filter import (
    KnowledgeIntent,
    MetadataFilterSpec,
    MetadataOperation,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import Document, DocumentProfile, User
from app.services.ingestion.profiling.rules import normalize_doc_number
from app.services.retrieval.metadata import (
    KnowledgeQueryPlanner,
    MetadataLookupService,
    render_metadata_answer,
)
from app.services.retrieval.metadata.compiler import compile_rows

TODAY = date(2026, 10, 20)
PEOPLE = [(unaccent_vietnamese("Trần Văn Bình"), "Trần Văn Bình")]


@pytest.fixture
def planner() -> KnowledgeQueryPlanner:
    return KnowledgeQueryPlanner()


# ----------------------------------------------------------------- planner


@pytest.mark.parametrize(
    ("question", "intent", "operation"),
    [
        ("Có bao nhiêu quyết định năm 2025?", KnowledgeIntent.METADATA, MetadataOperation.COUNT),
        ("Liệt kê các hợp đồng ký năm 2026", KnowledgeIntent.METADATA, MetadataOperation.LIST),
        ("Báo cáo tuần mới nhất là gì?", KnowledgeIntent.METADATA, MetadataOperation.LATEST),
        ("Tóm tắt báo cáo tuần mới nhất", KnowledgeIntent.METADATA_THEN_CONTENT, None),
        ("Quyết định 1119 quy định gì?", KnowledgeIntent.METADATA_THEN_CONTENT, None),
        # "bao nhiêu" about an amount inside a document stays on content RAG.
        ("Định mức chi hội nghị là bao nhiêu tiền?", KnowledgeIntent.CONTENT, None),
        ("Quy chế chi tiêu nội bộ quy định gì về công tác phí?", KnowledgeIntent.CONTENT, None),
    ],
)
def test_planner_intents(
    planner: KnowledgeQueryPlanner,
    question: str,
    intent: KnowledgeIntent,
    operation: MetadataOperation | None,
) -> None:
    plan = planner.plan(question, today=TODAY)
    assert plan.intent is intent, plan
    if operation is not None:
        assert plan.spec.operation is operation


def test_planner_filters(planner: KnowledgeQueryPlanner) -> None:
    plan = planner.plan(
        "Có bao nhiêu quyết định do ông Trần Văn Bình ký trong năm 2025?",
        today=TODAY,
        known_people=PEOPLE,
    )
    spec = plan.spec
    assert spec.categories == ("administrative",)
    assert spec.subtypes == ("Quyết định",)
    assert (spec.date_from, spec.date_to) == (date(2025, 1, 1), date(2025, 12, 31))
    assert spec.person == "Trần Văn Bình"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("báo cáo quý 2/2026", (date(2026, 4, 1), date(2026, 6, 30))),
        ("biên bản tháng 3/2026", (date(2026, 3, 1), date(2026, 3, 31))),
        ("hợp đồng năm nay", (date(2026, 1, 1), date(2026, 12, 31))),
        ("hợp đồng tháng trước", (date(2026, 9, 1), date(2026, 9, 30))),
    ],
)
def test_planner_date_ranges(
    planner: KnowledgeQueryPlanner, question: str, expected: tuple[date, date]
) -> None:
    spec = planner.plan(f"liệt kê {question}", today=TODAY).spec
    assert (spec.date_from, spec.date_to) == expected


def test_doc_number_with_slash(planner: KnowledgeQueryPlanner) -> None:
    plan = planner.plan("Nội dung chính của 80/QĐ-TNVN là gì?", today=TODAY)
    assert plan.spec.doc_number == "80/QĐ-TNVN"
    assert plan.intent is KnowledgeIntent.METADATA_THEN_CONTENT


# ---------------------------------------------------------------- compiler


def test_user_text_is_bound_not_inlined() -> None:
    spec = MetadataFilterSpec(person="x'; DROP TABLE documents; --", issuer="a%b")
    sql = str(compile_rows(spec, "11111111-1111-1111-1111-111111111111"))
    assert "DROP TABLE" not in sql
    assert "a%b" not in sql
    assert ":" in sql or "?" in sql  # bound parameters


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = "22222222-2222-2222-2222-222222222222"
    other = "33333333-3333-3333-3333-333333333333"

    def doc(i: int, user_id: str | None = None, active: bool = True) -> Document:
        return Document(
            id=f"doc-{i}",
            user_id=user_id,
            logical_document_id=f"log-{i}",
            source_type="upload",
            title=f"file-{i}.pdf",
            status="active" if active else "archived",
            is_active=active,
        )

    def prof(
        i: int,
        cat: str,
        sub: str | None,
        d: date | None,
        number: str | None = None,
        person: str | None = None,
    ) -> DocumentProfile:
        ents = (
            [
                {
                    "name": person,
                    "name_norm": unaccent_vietnamese(person),
                    "kind": "signer",
                    "role": "Ký",
                }
            ]
            if person
            else []
        )
        return DocumentProfile(
            document_id=f"doc-{i}",
            logical_document_id=f"log-{i}",
            doc_category=cat,
            doc_subtype=sub,
            title=f"Tài liệu {i}",
            doc_number=number,
            doc_number_norm=normalize_doc_number(number),
            doc_date=d,
            entities=ents,
            entity_names_norm=unaccent_vietnamese(person) if person else None,
            extractor_version="profile-v1",
        )

    async with factory.begin() as s:
        s.add_all([User(id=owner, email="o@x.local"), User(id=other, email="p@x.local")])
        s.add_all([doc(1), doc(2), doc(3), doc(4, active=False), doc(5, user_id=other), doc(6)])
        await s.flush()
        s.add_all(
            [
                prof(
                    1,
                    "administrative",
                    "Quyết định",
                    date(2025, 3, 1),
                    "1119/QĐ-TNVN",
                    "Trần Văn Bình",
                ),
                prof(2, "administrative", "Quyết định", date(2025, 8, 1), "11190/QĐ-TNVN"),
                prof(3, "report", "Báo cáo", date(2026, 10, 16)),
                prof(4, "administrative", "Quyết định", date(2025, 5, 1)),  # archived
                prof(5, "administrative", "Quyết định", date(2025, 6, 1)),  # other user's
                prof(6, "contract", "Hợp đồng", None),
            ]
        )
    yield factory
    await engine.dispose()


async def test_count_respects_active_owner_and_filters(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = MetadataLookupService(sessions)
    spec = MetadataFilterSpec(
        operation=MetadataOperation.COUNT,
        categories=("administrative",),
        date_from=date(2025, 1, 1),
        date_to=date(2025, 12, 31),
    )
    result = await service.run(spec, "22222222-2222-2222-2222-222222222222")
    # doc-4 archived and doc-5 owned by someone else are invisible.
    assert result.total == 2
    assert {r["document_id"] for r in result.rows} == {"doc-1", "doc-2"}


async def test_bare_number_matches_prefix_exactly(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = MetadataLookupService(sessions)
    result = await service.run(MetadataFilterSpec(doc_number="1119"), None)
    assert [r["document_id"] for r in result.rows] == ["doc-1"]


async def test_person_and_latest(sessions: async_sessionmaker[AsyncSession]) -> None:
    service = MetadataLookupService(sessions)
    by_person = await service.run(MetadataFilterSpec(person="tran van binh"), None)
    assert [r["document_id"] for r in by_person.rows] == ["doc-1"]
    latest = await service.run(MetadataFilterSpec(operation=MetadataOperation.LATEST), None)
    assert latest.rows[0]["document_id"] == "doc-3"  # newest dated; undated last


async def test_known_names_catalog_is_cached_even_when_empty(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = MetadataLookupService(sessions)
    people, _ = await service.known_names(None)
    assert ("tran van binh", "Trần Văn Bình") in people
    service._session_factory = None  # a second call must not touch the DB
    again, _ = await service.known_names(None)
    assert again == people


async def test_render_count_answer(sessions: async_sessionmaker[AsyncSession]) -> None:
    service = MetadataLookupService(sessions)
    plan = await service.plan("Có bao nhiêu quyết định năm 2025?", None, today=TODAY)
    result = await service.run(plan.spec, None)
    answer, citations = render_metadata_answer(plan.spec, result)
    assert answer.startswith("Có **2** quyết định năm 2025.")
    assert [c.document_id for c in citations] == ["doc-2", "doc-1"]
    assert citations[0].evidence_id == "doc:doc-2"

    empty_answer, empty_cites = render_metadata_answer(
        plan.spec, result.model_copy(update={"total": 0, "rows": ()})
    )
    assert empty_answer.startswith("Không tìm thấy quyết định nào năm 2025")
    assert empty_cites == []


async def test_number_matches_across_separator_styles(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = MetadataLookupService(sessions)
    result = await service.run(MetadataFilterSpec(doc_number="1119-QĐ/TNVN"), None)
    assert [r["document_id"] for r in result.rows] == ["doc-1"]
