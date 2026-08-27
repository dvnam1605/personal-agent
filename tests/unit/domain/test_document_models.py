"""Unit tests for P9A document ingestion domain contracts."""

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.domain.models.documents import (
    DetectedDocumentType,
    DetectionStatus,
    FingerprintInputs,
    IngestDecision,
    SourceDocument,
    StoredSourceState,
    TypeDetectionResult,
)


class TestSourceDocument:
    def test_minimal_valid_document(self) -> None:
        source = SourceDocument(source_id="abc", source_type="upload", filename="a.pdf")
        assert source.source_id == "abc"
        assert source.metadata == {}
        assert source.checksum is None

    def test_frozen_model(self) -> None:
        source = SourceDocument(source_id="abc", source_type="drive", filename="a.pdf")
        with pytest.raises(ValidationError):
            source.filename = "b.pdf"

    @pytest.mark.parametrize(
        "source_type", ["drive", "upload", "local_fixture", "preparsed_markdown"]
    )
    def test_all_source_types_accepted(self, source_type: str) -> None:
        source = SourceDocument(
            source_id="x", source_type=cast(Any, source_type), filename="f.docx"
        )
        assert source.source_type == source_type

    def test_rejects_unknown_source_type(self) -> None:
        with pytest.raises(ValidationError):
            SourceDocument(source_id="x", source_type=cast(Any, "ftp"), filename="f.pdf")

    @pytest.mark.parametrize("bad_filename", ["", "   "])
    def test_rejects_blank_filename(self, bad_filename: str) -> None:
        with pytest.raises(ValidationError):
            SourceDocument(source_id="x", source_type="upload", filename=bad_filename)

    def test_rejects_negative_size(self) -> None:
        with pytest.raises(ValidationError):
            SourceDocument(
                source_id="x",
                source_type="upload",
                filename="f.pdf",
                size_bytes=-1,
            )

    def test_full_document_round_trip(self) -> None:
        modified = datetime(2026, 8, 22, tzinfo=UTC)
        source = SourceDocument(
            source_id="drive-123",
            source_type="drive",
            filename="report.pdf",
            mime_type="application/pdf",
            modified_at=modified,
            size_bytes=1024,
            checksum="ab" * 32,
            external_uri="https://drive.example/file/123",
            metadata={"folder": "reports"},
        )
        dumped = source.model_dump()
        assert SourceDocument.model_validate(dumped) == source


class TestEnumsAndResults:
    def test_ingest_decision_values_match_spec(self) -> None:
        assert {member.value for member in IngestDecision} == {
            "NEW",
            "UNCHANGED",
            "MODIFIED",
            "DELETED",
            "RETRY_AFTER_FAILURE",
        }

    def test_detection_unsupported_result_has_no_type(self) -> None:
        result = TypeDetectionResult(status=DetectionStatus.UNSUPPORTED)
        assert result.document_type is None

    def test_detected_document_type_values(self) -> None:
        assert DetectedDocumentType.PDF.value == "pdf"
        assert DetectedDocumentType.DOCX.value == "docx"
        assert DetectedDocumentType.MARKDOWN.value == "markdown"


class TestFingerprintInputsDefaults:
    def test_defaults_mark_versions_unknown(self) -> None:
        inputs = FingerprintInputs(source_id="s1")
        assert inputs.parser_version == "unknown"
        assert inputs.parent_chunker_version == "unknown"
        assert inputs.child_chunker_version == "unknown"
        assert inputs.embedding_model == "unknown"
        assert inputs.embedding_dimensions == 1024


class TestStoredSourceState:
    def test_default_status_active(self) -> None:
        state = StoredSourceState(source_id="s1", fingerprint="a" * 64)
        assert state.status == "active"
        assert state.deleted is False

    def test_rejects_invalid_status(self) -> None:
        with pytest.raises(ValidationError):
            StoredSourceState(source_id="s1", fingerprint="a" * 64, status=cast(Any, "processing"))
