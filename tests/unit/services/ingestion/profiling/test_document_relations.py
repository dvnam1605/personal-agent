"""Relation extraction: rules over administrative text, signer fallback, LLM validation."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.ingestion.relations import DocumentRelationExtractor, RelationType
from app.services.ingestion.relations.extractor import signer_from_signature_block
from app.services.ingestion.relations.llm import parse_llm_relations
from app.services.ingestion.relations.rules import extract_rule_relations

DECISION = """Số: 90/QĐ-TNVN
TỔNG GIÁM ĐỐC ĐÀI TIẾNG NÓI VIỆT NAM

Căn cứ Luật thi đua, khen thưởng ngày 15/06/2022;

Căn cứ Nghi định số 152/2025/NĐ-CP ngày 14/6/2025 của Chính phủ về việc quy định về phân cấp;

Căn cứ Nghị định số 115/2020/NĐ-CP ngày 25/9/2020 của Chính phủ; Nghị định số 85/2023/NĐ-CP ngày 07/12/2023 của Chính phủ sửa đổi, bổ sung một số điều của Nghị định số 204/2004/NĐ-CP;

Căn cứ Luật Thực hành tiết kiệm, chống lãng phí số 44/2013/QH12 ngày 26/11/2013;

Theo đề nghị của Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế tại Tờ trình số 29/TTr-TCCBHTQT ngày 15/01/2026.
QUYẾT ĐỊNH:

Điều 1. Tặng Bằng khen của Tổng Giám đốc Đài Tiếng nói Việt Nam cho Ban Thư ký biên tập.

Điều 2. Kinh phí khen thưởng thực hiện theo Quyết định số 626/QĐ-TNVN ngày 18 tháng 3 năm 2025.

Điều 3. Quyết định này có hiệu lực kể từ ngày ký, thay thế Quyết định số 341/QĐ-TNVN ngày 23/02/2021.

Nơi nhận:

Như Điều 3;

Lưu: VT, TCCBHTQT. Căn cứ Quyết định số 9999/QĐ-TNVN;

TỔNG GIÁM ĐỐC

<div><img src="imgs/seal.jpg" alt="Image" /></div>

★Đỗ Tiên Sỹ
"""


def _by_type(text: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for d in extract_rule_relations(text):
        out.setdefault(d.relation_type.value, []).append(d)
    return out


def test_legal_basis_lines_become_based_on() -> None:
    bases = _by_type(DECISION)["based_on"]
    numbers = [d.target_number_norm for d in bases]
    # OCR "Nghi định" still counts; the number after a law name is found;
    # 204/2004 only describes what 85/2023 amends, so it is not a basis.
    assert numbers == [None, "1522025NDCP", "1152020NDCP", "852023NDCP", "442013QH12"]
    assert bases[0].target_text == "Luật thi đua, khen thưởng ngày 15/06/2022"
    assert bases[0].target_date == date(2022, 6, 15)
    assert bases[1].target_date == date(2025, 6, 14)


def test_proposal_funding_and_replacement() -> None:
    rels = _by_type(DECISION)
    (proposal,) = rels["proposed_by"]
    assert proposal.target_number_norm == "29TTRTCCBHTQT"
    assert proposal.attributes["proposer"] == "Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế"
    (funding,) = rels["funded_by"]
    assert funding.target_number_norm == "626QDTNVN"
    assert funding.target_date == date(2025, 3, 18)
    (replaced,) = rels["replaces"]
    assert replaced.target_number_norm == "341QDTNVN"


def test_recipients_block_is_ignored() -> None:
    numbers = {d.target_number_norm for d in extract_rule_relations(DECISION)}
    assert "9999QDTNVN" not in numbers


def test_self_reference_is_not_a_basis() -> None:
    texts = [
        d.target_text for d in extract_rule_relations("Căn cứ Quy chế này;\nCăn cứ Điều lệ Đảng;")
    ]
    assert texts == ["Điều lệ Đảng"]


def test_signer_fallback_reads_name_under_title() -> None:
    signer = signer_from_signature_block(DECISION)
    assert signer is not None
    assert signer.target_text == "Đỗ Tiên Sỹ"
    # OCR variants of one name fold to the same key.
    assert signer.target_norm == "do tien sy"


async def test_extractor_prefers_profile_signer_and_skips_non_administrative() -> None:
    extractor = DocumentRelationExtractor(use_llm=False)
    profile_signer = [{"name": "Đỗ Tiến Sỹ", "kind": "signer", "role": "Tổng Giám đốc"}]
    drafts = await extractor.aextract(DECISION, category="administrative", entities=profile_signer)
    signers = [d for d in drafts if d.relation_type is RelationType.SIGNED_BY]
    assert [(s.target_text, s.source) for s in signers] == [("Đỗ Tiến Sỹ", "profile")]

    thesis = await extractor.aextract(DECISION, category="academic", entities=[])
    assert thesis == []


@pytest.mark.parametrize(
    ("item", "kept"),
    [
        ({"type": "awards", "target": "Ban Thư ký biên tập", "target_kind": "organization"}, True),
        (
            {"type": "awards", "target": "Bà Ban Thư ký biên tập", "target_kind": "organization"},
            True,
        ),
        ({"type": "awards", "target": "Ban không có trong văn bản"}, False),  # hallucinated
        ({"type": "based_on", "target": "Ban Thư ký biên tập"}, False),  # rules own this type
        ({"type": "praises", "target": "Ban Thư ký biên tập"}, False),
    ],
)
def test_llm_reply_is_validated(item: dict, kept: bool) -> None:
    drafts = parse_llm_relations({"relations": [item]}, DECISION)
    assert bool(drafts) is kept
    if kept:
        assert drafts[0].target_text == "Ban Thư ký biên tập"
        assert drafts[0].source == "llm"


def test_llm_reply_garbage_is_empty() -> None:
    assert parse_llm_relations(None, DECISION) == []
    assert parse_llm_relations({"relations": "x"}, DECISION) == []
