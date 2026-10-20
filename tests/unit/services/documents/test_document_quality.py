"""Quality gates for generated documents: no meta-commentary, verified figures."""

from __future__ import annotations

from app.services.documents.quality import apply_quality_gates

EVIDENCE = (
    '<retrieved_document id="E1" number="664/QĐ-TNVN" date="2026-03-20">'
    "Số tiền: 500.000 đồng/người (Năm trăm nghìn đồng). Hà Nội, ngày 20 tháng 03 năm 2026."
    "</retrieved_document>"
)


def test_meta_commentary_is_removed() -> None:
    text = (
        "*Thông báo về tài liệu đối chiếu:* Tài liệu mã `doc-` không tìm thấy bằng chứng.\n"
        "Quyết định số 664/QĐ-TNVN tặng quà nhân ngày 8/3 [1].\n"
        "Bằng chứng hiện tại chưa cung cấp danh sách cụ thể."
    )
    cleaned, report = apply_quality_gates(text, EVIDENCE)
    assert cleaned == "Quyết định số 664/QĐ-TNVN tặng quà nhân ngày 8/3 [1]."
    assert len(report.removed_meta) == 2


def test_figures_must_appear_in_evidence() -> None:
    text = (
        "Mức quà là **500.000 đồng/người** [1]. Tổng kinh phí là 25.000.000 đồng [1].\n"
        "- Quyết định ký ngày 20/03/2026 [1]\n"
        "- Áp dụng cho 1.200 người lao động [1]"
    )
    cleaned, report = apply_quality_gates(text, EVIDENCE)
    assert "500.000 đồng/người" in cleaned and "20/03/2026" in cleaned
    assert "25.000.000" not in cleaned and "1.200" not in cleaned
    assert len(report.removed_unverified) == 2


def test_small_numbers_and_badges_are_not_checked() -> None:
    cleaned, report = apply_quality_gates("Điều 2 quy định 09 tập thể [3][4].", EVIDENCE)
    assert cleaned == "Điều 2 quy định 09 tập thể [3][4]."
    assert not report.removed_unverified


def test_heading_left_empty_is_dropped() -> None:
    text = "### 1. Mục đích\n\nKhông tìm thấy dữ liệu.\n\n### 2. Nội dung\n\nQuà 500.000 đồng [1]."
    cleaned, _ = apply_quality_gates(text, EVIDENCE)
    assert cleaned.startswith("### 2. Nội dung")
