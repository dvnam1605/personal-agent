"""Unit tests for OCR sidecar provenance and fingerprinting (H1)."""

import json
from pathlib import Path

from app.domain.models.ingestion.documents import FingerprintInputs, SourceDocument
from app.services.ingestion.fingerprint import compute_fingerprint
from app.services.ingestion.orchestrator import IngestionOrchestrator
from app.services.ingestion.source import build_local_source_document


def test_build_local_source_document_loads_sidecar(tmp_path: Path) -> None:
    md_file = tmp_path / "doc.md"
    md_file.write_text("# Test Document\n\nSome text.", encoding="utf-8")

    sidecar_file = tmp_path / "doc.ocr.json"
    sidecar_data = {
        "schema_version": 1,
        "source_file": "doc.pdf",
        "source_checksum": "a" * 64,
        "engine": "paddleocr_vl_1_6",
        "device": "cuda:0",
        "ocr_used": True,
        "pages_processed": 5,
        "warnings": [],
        "duration_seconds": 1.2,
        "dry_run": False,
        "finished_at": "2026-09-04T00:00:00Z",
    }
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    source = build_local_source_document(md_file, source_type="preparsed_markdown")
    assert "ocr_sidecar" in source.metadata
    assert source.metadata["ocr_sidecar"]["source_checksum"] == "a" * 64
    assert source.metadata["ocr_sidecar"]["engine"] == "paddleocr_vl_1_6"


def test_build_local_source_document_rejects_dry_run_and_missing_device(tmp_path: Path) -> None:
    md_file = tmp_path / "sample.md"
    md_file.write_text("# Title\n\nContent", encoding="utf-8")
    sidecar_file = tmp_path / "sample.ocr.json"

    # 1. dry_run: True should be rejected
    dry_sidecar = {
        "schema_version": 1,
        "source_file": "sample.pdf",
        "source_checksum": "a" * 64,
        "engine": "paddleocr_vl_1_6",
        "device": "cpu",
        "ocr_used": False,
        "pages_processed": 0,
        "dry_run": True,
        "finished_at": "2026-09-04T00:00:00Z",
    }
    sidecar_file.write_text(json.dumps(dry_sidecar), encoding="utf-8")
    doc_dry = build_local_source_document(md_file, source_type="preparsed_markdown")
    assert "ocr_sidecar" not in doc_dry.metadata

    # 2. missing/empty device should be rejected
    no_device_sidecar = {
        "schema_version": 1,
        "source_file": "sample.pdf",
        "source_checksum": "a" * 64,
        "engine": "paddleocr_vl_1_6",
        "device": "",
        "ocr_used": True,
        "pages_processed": 1,
        "dry_run": False,
        "finished_at": "2026-09-04T00:00:00Z",
    }
    sidecar_file.write_text(json.dumps(no_device_sidecar), encoding="utf-8")
    doc_no_device = build_local_source_document(md_file, source_type="preparsed_markdown")
    assert "ocr_sidecar" not in doc_no_device.metadata


def test_fingerprint_changes_when_ocr_sidecar_changes() -> None:
    base_inputs = FingerprintInputs(
        source_id="doc-1",
        checksum="mdhash123",
        parser_version="1.0",
        parent_chunker_version="1.0",
        child_chunker_version="1.0",
        embedding_model="model-1",
        embedding_dimensions=1024,
        ocr_source_checksum="a" * 64,
        ocr_engine="paddleocr_vl_1_6",
    )
    fp_base = compute_fingerprint(base_inputs)

    # Change PDF checksum -> fingerprint MUST change
    inputs_pdf_changed = base_inputs.model_copy(update={"ocr_source_checksum": "b" * 64})
    fp_pdf_changed = compute_fingerprint(inputs_pdf_changed)
    assert fp_base.fingerprint != fp_pdf_changed.fingerprint

    # Change OCR engine -> fingerprint MUST change
    inputs_engine_changed = base_inputs.model_copy(update={"ocr_engine": "surya_2"})
    fp_engine_changed = compute_fingerprint(inputs_engine_changed)
    assert fp_base.fingerprint != fp_engine_changed.fingerprint


def test_orchestrator_extracts_ocr_sidecar_into_fingerprint_inputs() -> None:
    source = SourceDocument(
        source_id="src-1",
        source_type="preparsed_markdown",
        filename="doc.md",
        checksum="hash1",
        metadata={
            "ocr_sidecar": {
                "source_checksum": "f" * 64,
                "engine": "paddleocr_vl_1_6",
            }
        },
    )
    # Instantiate orchestrator with dummy mocks
    orchestrator = IngestionOrchestrator(
        repository=None,  # type: ignore
        transaction=None,  # type: ignore
        embedding=None,  # type: ignore
    )
    inputs = orchestrator._fingerprint_inputs(source)
    assert inputs.ocr_source_checksum == "f" * 64
    assert inputs.ocr_engine == "paddleocr_vl_1_6"
