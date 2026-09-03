"""Unit tests for administrative metadata extraction & enrichment."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.models.administrative_metadata import AdministrativeMetadata
from app.domain.models.documents import SourceDocument
from app.services.ingestion.administrative_extractor import AdministrativeMetadataExtractor
from app.services.ingestion.chunking.engine import build_chunk_drafts
from app.services.ingestion.chunking.protocols import ChunkContext
from app.services.ingestion.parsing.markdown_parser import MarkdownDocumentParser

CORPUS_DIR = Path("data/QuyetDinh")


class TestAdministrativeMetadataExtractor:
    """Verifies regex and heuristic extraction across diverse document types."""

    def test_extract_dutoan_427(self) -> None:
        file_path = CORPUS_DIR / "DuToan/18-3-2026-954776_427QD_25_02_2026.md"
        assert file_path.exists(), f"File {file_path} must exist"
        content = file_path.read_text(encoding="utf-8")

        meta = AdministrativeMetadataExtractor.extract(content, filename=file_path.name)
        assert isinstance(meta, AdministrativeMetadata)
        assert meta.document_number == "427/QĐ-TNVN"
        assert meta.document_type == "Quyết định"
        assert meta.issuing_authority == "Đài Tiếng nói Việt Nam"
        assert meta.promulgation_date == "2026-02-25"
        assert meta.signer_name == "Vũ Hải Quang"
        assert meta.signer_role == "Phó Tổng Giám đốc"
        assert "phê duyệt dự toán" in (meta.subject or "").lower()
        assert meta.confidence >= 0.8

    def test_extract_nhansu_80(self) -> None:
        file_path = CORPUS_DIR / "NhanSu.TienLuong/14-5-2026-1125655_80QD_28_04_2026.md"
        assert file_path.exists()
        content = file_path.read_text(encoding="utf-8")

        meta = AdministrativeMetadataExtractor.extract(content, filename=file_path.name)
        assert meta.document_number == "80-QĐ/TNVN"
        assert meta.document_type == "Quyết định"
        assert meta.promulgation_date == "2026-04-28"
        assert meta.signer_name == "Vũ Hải Quang"
        assert meta.signer_role == "Phó Tổng Giám đốc"
        assert "chấm dứt hợp đồng" in (meta.subject or "").lower()

    def test_extract_chithi_1838(self) -> None:
        file_path = CORPUS_DIR / "ChiThi/30-8-2020-1431349_CT1838_23_07_2020.md"
        assert file_path.exists()
        content = file_path.read_text(encoding="utf-8")

        meta = AdministrativeMetadataExtractor.extract(content, filename=file_path.name)
        assert meta.document_number == "1838/CT-TNVN"
        assert meta.document_type == "Chỉ thị"
        assert meta.promulgation_date == "2020-07-23"
        assert meta.signer_name == "Ngô Minh Hiển"
        assert meta.signer_role == "Phó Tổng Giám đốc"
        assert "evfta" in (meta.subject or "").lower()

    def test_extract_thidua_1119(self) -> None:
        file_path = CORPUS_DIR / "ThiDuaKhenThuong/6-5-2026-1626379_1119QD_13_04_2026.md"
        assert file_path.exists()
        content = file_path.read_text(encoding="utf-8")

        meta = AdministrativeMetadataExtractor.extract(content, filename=file_path.name)
        assert meta.document_number == "1119/QĐ-TNVN"
        assert meta.document_type == "Quyết định"
        assert meta.promulgation_date == "2026-04-13"
        assert meta.signer_name == "Đỗ Tiến Sỹ"
        assert meta.signer_role == "Tổng Giám đốc"
        assert "bằng khen" in (meta.subject or "").lower()

    def test_extract_daotao_50(self) -> None:
        file_path = CORPUS_DIR / "DaoTao/14-5-2026-163037_50QD_23_04_2026.md"
        assert file_path.exists()
        content = file_path.read_text(encoding="utf-8")

        meta = AdministrativeMetadataExtractor.extract(content, filename=file_path.name)
        assert meta.document_number == "50/QĐ-TNVN"
        assert meta.promulgation_date == "2026-04-23"
        assert meta.signer_name == "Phạm Mạnh Hùng"
        assert meta.signer_role == "Phó Tổng Giám đốc"

    def test_filename_fallback_when_header_missing(self) -> None:
        """Verify fallback when OCR header box is missing but filename carries metadata."""
        sample_text = """
# QUYẾT ĐỊNH
Về việc khen thưởng tập thể xuất sắc
Căn cứ Nghị định số 46/2025/NĐ-CP;
KT. TỔNG GIÁM ĐỐC
PHÓ TỔNG GIÁM ĐỐC
Vũ Hải Quang
"""
        meta = AdministrativeMetadataExtractor.extract(
            sample_text,
            filename="29-1-2026-1520359_999QD_15_01_2026.md",
        )
        assert meta.document_number == "999/QĐ-TNVN"
        assert meta.promulgation_date == "2026-01-15"
        assert meta.signer_name == "Vũ Hải Quang"

    @pytest.mark.asyncio
    async def test_markdown_parser_and_chunk_propagation(self) -> None:
        """Verify end-to-end propagation from Markdown parser to parent and child drafts."""
        file_path = CORPUS_DIR / "DuToan/18-3-2026-954776_427QD_25_02_2026.md"
        raw_bytes = file_path.read_bytes()

        source = SourceDocument(
            source_id="src-dutoan-427",
            source_type="local_fixture",
            filename=file_path.name,
        )

        parser = MarkdownDocumentParser()
        parsed_doc = await parser.parse(source, raw_bytes)

        # Check ParsedDocumentMetadata
        admin_meta = parsed_doc.metadata.administrative_metadata
        assert isinstance(admin_meta, dict)
        assert admin_meta["document_number"] == "427/QĐ-TNVN"
        assert admin_meta["signer_name"] == "Vũ Hải Quang"
        assert admin_meta["promulgation_date"] == "2026-02-25"

        # Check ChunkDraftSet propagation
        context = ChunkContext(
            document_id="0df95f32-8df2-4752-9b2f-7634fef2662c",
            document_version_id="ver-427-01",
            source_id=source.source_id,
            title="Quyết định 427/QĐ-TNVN",
            filename=source.filename,
            mime_type="text/markdown",
            source_type="md",
        )

        draft_set = build_chunk_drafts(parsed_doc, context)
        assert len(draft_set.parents) > 0
        assert len(draft_set.children) > 0

        # Verify parent chunks carry metadata
        for parent in draft_set.parents:
            assert parent.administrative_metadata["document_number"] == "427/QĐ-TNVN"
            assert parent.administrative_metadata["signer_name"] == "Vũ Hải Quang"

        # Verify child chunks carry metadata
        for child in draft_set.children:
            assert child.administrative_metadata["document_number"] == "427/QĐ-TNVN"
            assert child.administrative_metadata["signer_name"] == "Vũ Hải Quang"

    def test_corpus_wide_extraction_rate(self) -> None:
        """Verifies that across all 37 real documents in data/QuyetDinh, extraction rate >= 90%."""
        all_md_files = list(CORPUS_DIR.rglob("*.md"))
        assert len(all_md_files) == 37

        extracted_numbers = 0
        extracted_dates = 0
        extracted_signers = 0

        for f in all_md_files:
            text = f.read_text(encoding="utf-8", errors="ignore")
            meta = AdministrativeMetadataExtractor.extract(text, filename=f.name)
            if meta.document_number:
                extracted_numbers += 1
            if meta.promulgation_date:
                extracted_dates += 1
            if meta.signer_name:
                extracted_signers += 1

        number_rate = extracted_numbers / len(all_md_files)
        date_rate = extracted_dates / len(all_md_files)
        signer_rate = extracted_signers / len(all_md_files)

        assert number_rate >= 0.90, f"Document number rate {number_rate:.2%} < 90%"
        assert date_rate >= 0.90, f"Promulgation date rate {date_rate:.2%} < 90%"
        assert signer_rate >= 0.65, f"Signer name rate {signer_rate:.2%} < 65%"
