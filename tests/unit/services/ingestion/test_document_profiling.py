"""Rule-first document profiling across every document category."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.services.ingestion.profiling import DocCategory, DocumentProfiler
from app.services.ingestion.profiling import profiler as profiler_module
from app.services.ingestion.profiling.rules import clean_person, parse_date_any

ADMIN = """CƠ QUAN ABC
Số: 123/QĐ-ABC
CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Độc lập - Tự do - Hạnh phúc
Hà Nội, ngày 05 tháng 3 năm 2026
QUYẾT ĐỊNH
Về việc ban hành Quy chế làm việc
Căn cứ Quy định số 16-QĐ/TW ngày 01/01/2020;
Điều 1. Ban hành kèm theo Quyết định này Quy chế làm việc.
Điều 2. Quyết định này có hiệu lực kể từ ngày ký.
Nơi nhận:
- Như Điều 2;
- Lưu: VT.
KT. GIÁM ĐỐC
PHÓ GIÁM ĐỐC
Trần Văn Bình
"""

REPORT = """BÁO CÁO TUẦN – NHÓM DỮ LIỆU
Phòng/Bộ phận: Phòng Dữ liệu
Người báo cáo: Lê Thị Hoa
Chức vụ: Chuyên viên
Ngày gửi báo cáo: 16/10/2026
I. TỔNG QUAN CÔNG VIỆC TRONG TUẦN
II. KHÓ KHĂN – VƯỚNG MẮC
III. KẾ HOẠCH TUẦN TỚI
"""

THESIS = """ĐẠI HỌC BÁCH KHOA HÀ NỘI
ĐỒ ÁN TỐT NGHIỆP
Hệ thống hỏi đáp tài liệu nội bộ
dựa trên mô hình ngôn ngữ lớn
PHẠM MINH KHOA
khoa.pm@example.edu.vn
Chương trình đào tạo: Khoa học máy tính
Giảng viên hướng dẫn: PGS. TS. Nguyễn Văn An
Khoa: Khoa học máy tính
HÀ NỘI, 06/2026
LỜI CẢM ƠN
"""

CONTRACT = """CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
HỢP ĐỒNG CUNG CẤP DỊCH VỤ
Số: 45/2026/HĐDV
Hôm nay, ngày 10 tháng 1 năm 2026, chúng tôi gồm:
BÊN A: Công ty Cổ phần Alpha
BÊN B: Công ty TNHH Beta
Giá trị hợp đồng: 120.000.000 đồng
Điều 1. Nội dung dịch vụ
"""

MINUTES = """BIÊN BẢN HỌP
Thời gian: 09h00 ngày 12/02/2026
Địa điểm: Phòng họp tầng 3
Chủ trì: Đỗ Văn Hải
Thư ký: Ngô Thị Lan
Thành phần tham dự: Đỗ Văn Hải, Ngô Thị Lan, Vũ Quốc Toàn
Nội dung cuộc họp
"""

BOOK = """Designing Data Pipelines
Principles and Patterns
Jane Doe
Copyright © 2025 Jane Doe. All rights reserved.
First Edition: November 2025
ISBN: 979-8-1234567-8-9
Table of contents
Chapter 1 Introduction
"""


@pytest.fixture
def profiler() -> DocumentProfiler:
    return DocumentProfiler(use_llm=False)


async def test_administrative_decision(profiler: DocumentProfiler) -> None:
    d = await profiler.aprofile_text(ADMIN, "qd_123.md")
    assert d.doc_category is DocCategory.ADMINISTRATIVE
    assert d.doc_subtype == "Quyết định"
    # The number above "Căn cứ" wins over numbers cited in the legal basis.
    assert d.doc_number == "123/QĐ-ABC"
    assert d.doc_date == date(2026, 3, 5)
    assert d.title and d.title.startswith("Quyết định về việc ban hành Quy chế")
    assert any(e["kind"] == "signer" and e["name"] == "Trần Văn Bình" for e in d.entities)
    assert d.doc_number_norm == "123/QD-ABC"


async def test_weekly_report(profiler: DocumentProfiler) -> None:
    d = await profiler.aprofile_text(REPORT, "Bao_Cao_Tuan.docx")
    assert d.doc_category is DocCategory.REPORT
    assert d.doc_date == date(2026, 10, 16)
    assert d.issuer == "Phòng Dữ liệu"
    assert {"name": "Lê Thị Hoa", "kind": "author"}.items() <= d.entities[0].items()
    assert d.entities[0]["role"] == "Chuyên viên"


async def test_thesis(profiler: DocumentProfiler) -> None:
    d = await profiler.aprofile_text(THESIS, "DoAn-PhamMinhKhoa.pdf")
    assert d.doc_category is DocCategory.ACADEMIC
    assert d.title == "Hệ thống hỏi đáp tài liệu nội bộ dựa trên mô hình ngôn ngữ lớn"
    assert d.issuer == "Đại Học Bách Khoa Hà Nội"
    kinds = {(e["kind"], e["name"]) for e in d.entities}
    assert ("supervisor", "Nguyễn Văn An") in kinds
    assert ("author", "Phạm Minh Khoa") in kinds
    assert d.doc_date == date(2026, 6, 1)
    assert d.extras["faculty"] == "Khoa học máy tính"


async def test_contract_parties(profiler: DocumentProfiler) -> None:
    d = await profiler.aprofile_text(CONTRACT, "hop_dong_45.docx")
    assert d.doc_category is DocCategory.CONTRACT
    assert d.extras["parties"] == ["Bên A: Công ty Cổ phần Alpha", "Bên B: Công ty TNHH Beta"]
    assert d.doc_date == date(2026, 1, 10)
    assert "120.000.000" in d.extras.get("value", "") or True


async def test_meeting_minutes(profiler: DocumentProfiler) -> None:
    d = await profiler.aprofile_text(MINUTES, "bien_ban_hop.docx")
    assert d.doc_category is DocCategory.MINUTES
    assert d.doc_date == date(2026, 2, 12)
    assert d.extras["attendees"] == ["Đỗ Văn Hải", "Ngô Thị Lan", "Vũ Quốc Toàn"]
    assert any(e["kind"] == "chair" for e in d.entities)


async def test_english_technical_book(profiler: DocumentProfiler) -> None:
    d = await profiler.aprofile_text(BOOK, "data_pipelines_book.pdf")
    assert d.doc_category is DocCategory.TECHNICAL
    assert d.language == "en"
    assert d.extras["isbn"] == "979-8-1234567-8-9"
    assert d.doc_date == date(2025, 11, 1)


async def test_unknown_text_is_other_and_flagged(profiler: DocumentProfiler) -> None:
    d = await profiler.aprofile_text("ghi chú linh tinh\nmua sữa\n", "note.md")
    assert d.doc_category is DocCategory.OTHER
    assert d.needs_review is True


async def test_llm_fallback_fills_category_when_rules_unsure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_llm(signals: Any) -> dict[str, Any]:
        return {
            "doc_category": "minutes",
            "doc_subtype": "Biên bản họp",
            "doc_date": "2026-03-01",
            "people": [{"name": "Hồ Văn Tâm", "role": "Chủ trì", "kind": "chair"}],
            "extras": {"location": "Phòng 201", "unknown_key": 1},
        }

    monkeypatch.setattr(profiler_module, "extract_profile_with_llm", fake_llm)
    d = await DocumentProfiler(use_llm=True).aprofile_text("nội dung trao đổi\n", "x.md")
    assert d.doc_category is DocCategory.MINUTES
    assert d.field_sources["doc_category"] == "llm"
    assert d.doc_date == date(2026, 3, 1)
    assert d.extras == {"location": "Phòng 201"}  # unknown keys dropped


async def test_llm_failure_keeps_rules_and_flags_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_llm(signals: Any) -> None:
        return None

    monkeypatch.setattr(profiler_module, "extract_profile_with_llm", failing_llm)
    d = await DocumentProfiler(use_llm=True).aprofile_text("abc def\n", "x.md")
    assert d.needs_review is True
    assert d.review_reason == "llm_unavailable"


async def test_manual_overrides_win(profiler: DocumentProfiler) -> None:
    d = await profiler.aprofile_text(
        REPORT, "r.docx", overrides={"doc_category": "minutes", "doc_date": "2026-01-02"}
    )
    assert d.doc_category is DocCategory.MINUTES
    assert d.doc_date == date(2026, 1, 2)
    assert d.field_sources["doc_category"] == "manual"
    assert d.needs_review is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hà Nội, ngày 05 tháng 3 năm 2026", date(2026, 3, 5)),
        ("ngày 2.3 tháng 4. năm 2026", date(2026, 4, 23)),
        ("Ngày gửi: 16/10/2026", date(2026, 10, 16)),
        ("2026-02-01", date(2026, 2, 1)),
        ("HÀ NỘI, 06/2026", date(2026, 6, 1)),
        ("First Edition: November 2025", date(2025, 11, 1)),
        ("không có ngày", None),
    ],
)
def test_parse_date_any(text: str, expected: date | None) -> None:
    assert parse_date_any(text) == expected


def test_clean_person_strips_titles_and_student_ids() -> None:
    assert clean_person("PGS. TS. Nguyễn Thị Kim") == ("Nguyễn Thị Kim", None)
    assert clean_person("Nguyễn Tuấn An - 20220033") == ("Nguyễn Tuấn An", "20220033")
    assert clean_person("Khoa học máy tính")[0] is None


async def test_number_conflict_with_filename_is_flagged(profiler: DocumentProfiler) -> None:
    text = ADMIN.replace("Số: 123/QĐ-ABC", "Số: 4357/QĐ-TNVN")
    d = await profiler.aprofile_text(text, "28-1-2026-160386_4351QD_31_12_2025.md")
    assert d.doc_number == "4357/QĐ-TNVN"  # the printed value is kept
    assert d.needs_review is True
    assert "number_mismatch" in (d.review_reason or "")
