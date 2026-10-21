"""Relation route: planning relation questions and answering them from document_relations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.text import unaccent_vietnamese
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import Document, DocumentProfile, DocumentRelation, User
from app.services.ingestion.profiling.rules import normalize_doc_number
from app.services.ingestion.relations import RelationType
from app.services.retrieval.relations import RelationLookupService, RelationQueryPlanner

TODAY = date(2026, 10, 20)


# ----------------------------------------------------------------- planner


@pytest.mark.parametrize(
    ("question", "types"),
    [
        ("Quyết định 29/QĐ-TNVN thay thế quyết định nào?", {RelationType.REPLACES}),
        ("Những quyết định nào căn cứ Nghị định 152/2025/NĐ-CP?", {RelationType.BASED_ON}),
        ("Ông Trần Văn Bình đã ký những văn bản nào?", {RelationType.SIGNED_BY}),
        (
            "Ai ký các quyết định tặng Bằng khen năm 2026?",
            {RelationType.SIGNED_BY, RelationType.AWARDS},
        ),
    ],
)
def test_planner_detects_relations(question: str, types: set[RelationType]) -> None:
    plan = RelationQueryPlanner().plan(
        question, today=TODAY, known_names=[unaccent_vietnamese("Trần Văn Bình")]
    )
    assert plan is not None
    assert set(plan.types) == types


@pytest.mark.parametrize(
    "question",
    [
        "Có bao nhiêu quyết định năm 2025?",  # no relation → metadata route
        "Mức quà tặng nữ viên chức nhân ngày 8/3 là bao nhiêu?",
        "Kỹ thuật phát sóng của trạm Cột 5 là gì?",  # "kỹ" is not "ký"
        # Relation but neither anchored nor a list: the subject names one
        # document, which only RAG can resolve.
        "Danh mục mã định danh các đơn vị ban hành năm 2026 thay thế quyết định nào?",
    ],
)
def test_planner_leaves_other_questions_alone(question: str) -> None:
    assert RelationQueryPlanner().plan(question, today=TODAY) is None


def test_number_direction_and_date_in_question() -> None:
    planner = RelationQueryPlanner()
    forward = planner.plan(
        "Quyết định 29/QĐ-TNVN thay thế quyết định nào?",
        today=TODAY,
        is_source_number=lambda raw: raw.startswith("29"),
    )
    assert forward is not None and forward.numbers[0].is_source
    reverse = planner.plan(
        "Quyết định 852/QĐ-TNVN ngày 09/4/2025 đã bị thay thế bởi văn bản nào?",
        today=TODAY,
    )
    assert reverse is not None and not reverse.numbers[0].is_source
    # The date describes the anchored document; it must not filter sources.
    assert reverse.date_from is None and reverse.date_to is None


# ----------------------------------------------------------------- service


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    other = "33333333-3333-3333-3333-333333333333"

    def doc(i: int, number: str, d: date, user_id: str | None = None, active: bool = True):
        return [
            Document(
                id=f"doc-{i}",
                user_id=user_id,
                logical_document_id=f"log-{i}",
                source_type="upload",
                title=f"file-{i}.md",
                status="active" if active else "archived",
                is_active=active,
            ),
            DocumentProfile(
                document_id=f"doc-{i}",
                logical_document_id=f"log-{i}",
                doc_category="administrative",
                title=f"Quyết định {number}",
                doc_number=number,
                doc_number_norm=normalize_doc_number(number),
                doc_date=d,
                extractor_version="profile-v7",
            ),
        ]

    def rel(i: int, relation: RelationType, target: str, kind: str = "document", **attrs):
        number = target.split(" số ")[-1].split()[0] if " số " in target else None
        return DocumentRelation(
            document_id=f"doc-{i}",
            logical_document_id=f"log-{i}",
            relation_type=relation.value,
            target_kind=kind,
            target_text=target,
            target_norm=unaccent_vietnamese(target),
            target_number_norm=normalize_doc_number(number) if number else None,
            attributes=attrs,
            source="rule",
            extractor_version="relations-v1",
        )

    async with factory.begin() as s:
        s.add(User(id=other, email="p@x.local"))
        s.add_all(
            doc(1, "90/QĐ-TNVN", date(2026, 1, 16)) + doc(2, "1119/QĐ-TNVN", date(2026, 4, 13))
        )
        s.add_all(doc(3, "56-QĐ/TNVN", date(2026, 4, 23)))
        s.add_all(doc(4, "77/QĐ-TNVN", date(2026, 5, 1), active=False))
        s.add_all(doc(5, "78/QĐ-TNVN", date(2026, 5, 2), user_id=other))
        await s.flush()
        nd152 = "Nghị định số 152/2025/NĐ-CP ngày 14/6/2025"
        s.add_all(
            [
                rel(1, RelationType.BASED_ON, nd152),
                rel(2, RelationType.BASED_ON, nd152),
                rel(4, RelationType.BASED_ON, nd152),  # archived version
                rel(5, RelationType.BASED_ON, nd152),  # other user's document
                rel(3, RelationType.REPLACES, "Quyết định số 341/QĐ-TNVN ngày 23/02/2021"),
                rel(1, RelationType.SIGNED_BY, "Đỗ Tiến Sỹ", "person"),
                rel(2, RelationType.SIGNED_BY, "Dõ Tiến Sỹ", "person"),  # OCR variant
                rel(2, RelationType.AWARDS, "Ban Thư ký biên tập", "organization"),
                rel(2, RelationType.AWARDS, "Văn phòng", "organization"),
                rel(1, RelationType.AWARDS, "Đoàn Thanh niên", "organization"),
            ]
        )
    yield factory
    await engine.dispose()


async def test_reverse_basis_respects_visibility(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    svc = RelationLookupService(sessions)
    result = await svc.answer(
        "Những quyết định nào căn cứ Nghị định 152/2025/NĐ-CP?", None, today=TODAY
    )
    assert result is not None
    assert result.total == 2  # archived and other-user documents are hidden
    assert {c.document_id for c in result.citations} == {"doc-1", "doc-2"}
    assert "152/2025/NĐ-CP" in result.answer


async def test_forward_and_reverse_replacement(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    svc = RelationLookupService(sessions)
    forward = await svc.answer("Quyết định 56-QĐ/TNVN thay thế quyết định nào?", None, today=TODAY)
    reverse = await svc.answer("Văn bản nào đã thay thế Quyết định 341/QĐ-TNVN?", None, today=TODAY)
    for result in (forward, reverse):
        assert result is not None
        assert [c.document_id for c in result.citations] == ["doc-3"]
        assert "341/QĐ-TNVN" in result.answer


async def test_signer_lookup_folds_ocr_variants(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    svc = RelationLookupService(sessions)
    result = await svc.answer("Ông Đỗ Tiến Sỹ đã ký những văn bản nào?", None, today=TODAY)
    assert result is not None
    assert {c.document_id for c in result.citations} == {"doc-1", "doc-2"}


async def test_anchored_award_shows_only_matching_target(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    svc = RelationLookupService(sessions)
    result = await svc.answer(
        "Ban Thư ký biên tập được tặng Bằng khen theo quyết định nào?", None, today=TODAY
    )
    assert result is not None
    assert [c.document_id for c in result.citations] == ["doc-2"]
    assert "Ban Thư ký biên tập" in result.answer
    assert "Văn phòng" not in result.answer


async def test_who_signed_award_decisions_in_a_year(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    svc = RelationLookupService(sessions)
    result = await svc.answer("Ai ký các quyết định tặng Bằng khen năm 2026?", None, today=TODAY)
    assert result is not None
    assert {c.document_id for c in result.citations} == {"doc-1", "doc-2"}
    assert "người ký" in result.answer


async def test_no_matching_fact_falls_through(sessions: async_sessionmaker[AsyncSession]) -> None:
    svc = RelationLookupService(sessions)
    assert (
        await svc.answer("Văn bản nào thay thế Quyết định 1275/QĐ-TNVN?", None, today=TODAY) is None
    )
    assert await svc.answer("Có bao nhiêu quyết định năm 2026?", None, today=TODAY) is None
