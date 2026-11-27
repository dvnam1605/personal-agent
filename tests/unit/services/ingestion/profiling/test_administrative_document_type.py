"""Document type detection: whole filename tokens, header lines, and no guessed default."""

from __future__ import annotations

import pytest

from app.services.ingestion.profiling.administrative_extractor import (
    AdministrativeMetadataExtractor as Ext,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("VOV_CT12_TNVN.md", "Chỉ thị"),
        ("45CT_2024.pdf", "Chỉ thị"),
        ("so_1139QD_TNVN.md", "Quyết định"),
        ("quyetdinh-moi.docx", "Quyết định"),
    ],
)
def test_filename_tokens_name_the_type(filename: str, expected: str) -> None:
    assert Ext._extract_document_type("", filename) == expected


def test_filename_token_must_match_whole() -> None:
    assert Ext._extract_document_type("", "bao_cao_CTSV_2024.pdf") is None
    assert Ext._extract_document_type("", "QDA_report.pdf") is None


def test_header_line_names_the_type() -> None:
    assert Ext._extract_document_type("ĐÀI TIẾNG NÓI\nTHÔNG BÁO\nVề việc nghỉ lễ", "") == (
        "Thông báo"
    )


def test_unknown_text_has_no_type() -> None:
    assert Ext._extract_document_type("Báo cáo tổng kết năm", "report.pdf") is None
    assert Ext.extract("Báo cáo tổng kết năm", filename="report.pdf").document_type is None


def test_header_scan_stops_at_legal_basis() -> None:
    header = "Căn cứ Luật Tổ chức\nQUYẾT ĐỊNH"
    assert Ext._extract_document_type(header, "") is None


def test_extract_fields_without_type_keeps_subject_and_signer() -> None:
    header = "ĐÀI TIẾNG NÓI VIỆT NAM\nSố: 12/TB-TNVN\nVề việc nghỉ lễ; căn cứ\nNội dung"
    tail = "GIÁM ĐỐC\nNguyễn Văn An"
    fields = Ext.extract_fields(header, tail, "a.pdf")
    assert fields.document_type is None
    assert fields.subject == "Về việc nghỉ lễ"
    assert fields.subject_source == "rule"
    assert fields.document_number == "12/TB-TNVN"
    assert (fields.signer_name, fields.signer_role) == ("Nguyễn Văn An", "Giám đốc")


def test_extract_fields_rebuilds_subject_from_article_one() -> None:
    text = "Điều 1. Tặng Bằng khen của Tổng Giám đốc cho ông A"
    fields = Ext.extract_fields("ĐÀI", text, "", "Quyết định")
    assert fields.subject_source == "rule_article1"
    assert fields.subject and fields.subject.startswith("Về việc tặng Bằng khen")
    assert fields.document_type == "Quyết định"
