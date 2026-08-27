"""Unit tests for source discovery helpers (spec P9A-2)."""

from pathlib import Path

from app.services.ingestion.source import (
    build_local_source_document,
    checksum_bytes,
    checksum_file,
)


class TestChecksums:
    def test_checksum_bytes_known_vector(self) -> None:
        import hashlib

        content = b"hello ingestion"
        assert checksum_bytes(content) == hashlib.sha256(content).hexdigest()

    def test_checksum_file_matches_bytes_checksum(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.pdf"
        path.write_bytes(b"%PDF-1.7 fake body " * 1000)
        assert checksum_file(path) == checksum_bytes(path.read_bytes())

    def test_different_content_different_checksum(self) -> None:
        assert checksum_bytes(b"a") != checksum_bytes(b"b")


class TestBuildLocalSourceDocument:
    def test_builds_from_real_file(self, tmp_path: Path) -> None:
        path = tmp_path / "report.pdf"
        path.write_bytes(b"%PDF-1.7 payload")
        source = build_local_source_document(path)
        assert source.filename == "report.pdf"
        assert source.source_type == "local_fixture"
        assert source.size_bytes == len(b"%PDF-1.7 payload")
        assert source.checksum == checksum_bytes(b"%PDF-1.7 payload")
        assert source.modified_at is not None
        assert source.metadata == {}

    def test_explicit_source_id_and_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        path.write_text("# hi", encoding="utf-8")
        source = build_local_source_document(
            path,
            source_type="preparsed_markdown",
            source_id="md-1",
            metadata={"engine": "paddleocr_vl_1_6"},
        )
        assert source.source_id == "md-1"
        assert source.source_type == "preparsed_markdown"
        assert source.metadata["engine"] == "paddleocr_vl_1_6"

    def test_default_source_id_is_filename(self, tmp_path: Path) -> None:
        path = tmp_path / "unique_name.pdf"
        path.write_bytes(b"x")
        assert build_local_source_document(path).source_id == "unique_name.pdf"
