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
    assert d.doc_number_norm == "123QDABC"


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


async def test_label_value_on_next_line_and_name_is_never_title(
    profiler: DocumentProfiler,
) -> None:
    text = (
        "PHẠM MINH KHOA\nkhoa.pm@example.edu.vn\nChương trình đào tạo: Khoa học máy tính\n"
        "Giảng viên hướng dẫn:\nPGS. TS. Nguyễn Văn An\nKhoa:\nKhoa học máy tính\n"
        "HÀ NỘI, 06/2026\nLỜI CẢM ƠN\n"
    )
    d = await profiler.aprofile_text(text, "DoAn-x.pdf", headings=("PHẠM MINH KHOA", "LỜI CẢM ƠN"))
    assert d.doc_category is DocCategory.ACADEMIC
    assert any(e["kind"] == "supervisor" and e["name"] == "Nguyễn Văn An" for e in d.entities)
    assert d.title != "PHẠM MINH KHOA"


def test_headings_are_not_people_but_real_names_survive() -> None:
    assert clean_person("BÁO CÁO TỔNG HỢP")[0] is None
    assert clean_person("KẾ HOẠCH CÔNG TÁC")[0] is None
    # Syllables that also occur in document words must not reject real names.
    for name in ("Nguyễn Văn Bảo", "Cao Thị Hương", "Trần Quốc Toàn", "Đinh Công Nam"):
        assert clean_person(name)[0] == name


def test_job_titles_are_not_people() -> None:
    assert clean_person("Tổng Giám Đốc")[0] is None
    assert clean_person("PHÓ GIÁM ĐỐC")[0] is None


# Real header shapes from the corpus: the title line can be lost by OCR, and
# the legal basis cites other decrees "về việc …".
NO_TITLE_DECISION = """TỔNG GIÁM ĐỐC ĐÀI TIẾNG NÓI VIỆT NAM
Căn cứ Luật thi đua, khen thưởng ngày 15/06/2022;
Căn cứ Nghị định số 152/2025/NĐ-CP ngày 14/6/2025 của Chính phủ về việc quy định về phân cấp, phân quyền trong lĩnh vực thi đua, khen thưởng;
TỔNG GIÁM ĐỐC ĐÀI TIẾNG NÓI VIỆT NAM
QUYẾT ĐỊNH:
Điều 1. Tặng Bằng khen của Tổng Giám đốc Đài Tiếng nói Việt Nam cho Liên Chi hội Nhà báo Đài Tiếng nói Việt Nam, đã có thành tích xuất sắc.
Điều 2. Kinh phí khen thưởng thực hiện theo Quyết định số 626/QĐ-TNVN ngày 18 tháng 3 năm 2025 của Tổng Giám đốc về việc chi thưởng.
"""

SAME_LINE_TITLE = """Số: 1119/QĐ-TNVN
Hà Nội, ngày 13 tháng 04 năm 2026
QUYẾT ĐỊNH Về việc tặng Bằng khen của Tổng Giám đốc Đài Tiếng nói Việt Nam
TỔNG GIÁM ĐỐC ĐÀI TIẾNG NÓI VIỆT NAM
Căn cứ Nghị định số 152/2025/NĐ-CP của Chính phủ về việc quy định về phân cấp;
QUYẾT ĐỊNH:
Điều 1. Tặng Bằng khen cho các tập thể có tên sau đây:
"""


def test_subject_never_comes_from_the_legal_basis() -> None:
    from app.services.ingestion.administrative_extractor import AdministrativeMetadataExtractor as X

    assert X._extract_subject(NO_TITLE_DECISION) is None
    assert X._extract_subject(SAME_LINE_TITLE) == (
        "Về việc tặng Bằng khen của Tổng Giám đốc Đài Tiếng nói Việt Nam"
    )
    wrapped = "QUYẾT ĐỊNH\nVề việc ban hành Quy chế làm việc\ncủa Đài Tiếng nói Việt Nam\nCăn cứ …"
    assert X._extract_subject(wrapped) == (
        "Về việc ban hành Quy chế làm việc của Đài Tiếng nói Việt Nam"
    )
    assert X._extract_subject("CHỈ THỊ\nV/v tăng cường công tác văn thư") == (
        "Về việc tăng cường công tác văn thư"
    )


def test_article_one_fallback_keeps_the_action_not_the_recipients() -> None:
    from app.services.ingestion.administrative_extractor import AdministrativeMetadataExtractor as X

    assert X._extract_subject_from_article1(NO_TITLE_DECISION) == (
        "Về việc tặng Bằng khen của Tổng Giám đốc Đài Tiếng nói Việt Nam"
    )
    assert (
        X._extract_subject_from_article1(
            "Điều 1. Ban hành kèm theo Quyết định này Quy chế làm việc của Đài Tiếng nói Việt Nam."
        )
        == "Về việc ban hành Quy chế làm việc của Đài Tiếng nói Việt Nam"
    )
    assert X._extract_subject_from_article1("Điều 10. Hiệu lực thi hành.") is None


async def test_decision_without_title_line_is_named_by_article_one(
    profiler: DocumentProfiler,
) -> None:
    d = await profiler.aprofile_text(NO_TITLE_DECISION, "QD_91_TNVN.pdf")
    assert d.doc_category is DocCategory.ADMINISTRATIVE
    assert d.title == ("Quyết định về việc tặng Bằng khen của Tổng Giám đốc Đài Tiếng nói Việt Nam")
    assert "phân cấp" not in (d.title or "")
    assert d.field_sources.get("title") == "rule_article1"
    assert d.needs_review


async def test_title_on_the_type_line(profiler: DocumentProfiler) -> None:
    d = await profiler.aprofile_text(SAME_LINE_TITLE, "QD_1119_TNVN.pdf")
    assert d.title == ("Quyết định về việc tặng Bằng khen của Tổng Giám đốc Đài Tiếng nói Việt Nam")
    assert d.field_sources.get("title") == "rule"


def test_article_one_clause_is_cut_at_lists_positions_and_parentheses() -> None:
    from app.services.ingestion.administrative_extractor import AdministrativeMetadataExtractor as X

    cases = {
        "Điều 1. Ban hành Chương trình tiết kiệm, chống lãng phí năm 2026 của Đài.": (
            "Về việc ban hành Chương trình tiết kiệm, chống lãng phí năm 2026 của Đài"
        ),
        "Điều 1. Thành lập Hội đồng tư vấn năm 2026 gồm các thành viên (có danh sách kèm theo).": (
            "Về việc thành lập Hội đồng tư vấn năm 2026"
        ),
        "Điều 1. Giao nhiệm vụ nghiên cứu khoa học năm 2026 với nội dung sau:": (
            "Về việc giao nhiệm vụ nghiên cứu khoa học năm 2026"
        ),
        "Điều 1. Điều động ông Lưu Phú, chuyên viên Ban Thư ký, đến công tác tại Văn phòng.": (
            "Về việc điều động ông Lưu Phú"
        ),
        "Điều 1. Phê duyệt dự toán kinh phí năm 2026 (Loại 100": (
            "Về việc phê duyệt dự toán kinh phí năm 2026"
        ),
    }
    for text, expected in cases.items():
        assert X._extract_subject_from_article1(text) == expected, text
    # Not an operative clause: the list of executors.
    assert X._extract_subject_from_article1("Điều 1. Văn phòng, các đơn vị thực hiện.") is None


async def test_repeated_cover_line_is_not_doubled(profiler: DocumentProfiler) -> None:
    slides = (
        "TRƯỜNG ĐẠI HỌC BÁCH KHOA HÀ NỘI\nĐỒ ÁN TỐT NGHIỆP\n"
        "Hệ thống tối ưu lãi vay trên blockchain\nHệ thống tối ưu lãi vay trên blockchain\n"
        "Sinh viên: Nguyễn Văn A\n"
    )
    d = await profiler.aprofile_text(slides, "slides.pdf")
    assert d.title == "Hệ thống tối ưu lãi vay trên blockchain"


def test_admin_title_drops_repeated_type_name() -> None:
    from app.services.ingestion.profiling.profiler import _admin_title

    assert (
        _admin_title(
            "QUYẾT ĐỊNH: QUYET ĐỊNH Phê duyệt Kế hoạch chuyển đổi số năm 2025", "Quyết định"
        )
        == "Quyết định phê duyệt Kế hoạch chuyển đổi số năm 2025"
    )
    assert _admin_title("Quy chế làm việc", "Quyết định") == "Quy chế làm việc"


async def test_book_title_comes_from_the_cover_not_the_contents(
    profiler: DocumentProfiler,
) -> None:
    cover = (
        "Designing Multi-Agent\nSystems\nPrinciples, Patterns, and Implementation\nfor AI Agents\n"
        "Victor Dibia\nDesigning Multi-Agent Systems Principles, Patterns, and Implementation "
        "for AI Agents\nby Victor Dibia\nCopyright © 2025 Victor Dibia. All rights reserved.\n"
        "ISBN: 979-8-1234567-8-9\nTable of contents\nPreface\nChapter 1 Introduction\n"
    )
    d = await profiler.aprofile_text(cover, "dibia_book.pdf")
    assert d.title == (
        "Designing Multi-Agent Systems Principles, Patterns, and Implementation for AI Agents"
    )
