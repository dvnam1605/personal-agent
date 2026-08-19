"""Unit tests for Evidence models."""

import pytest
from pydantic import ValidationError

from app.domain.enums import EvidenceType
from app.domain.models import EvidenceItem, EvidenceSource


def test_evidence_source_and_item_creation() -> None:
    """Verify source identity preservation and default generation."""
    source = EvidenceSource(
        source_type="gmail_message",
        source_id="msg_12345",
        uri="https://mail.google.com/mail/u/0/#inbox/msg_12345",
        title="Project sync follow-up",
        metadata={"sender": "alice@example.com", "thread_id": "thread_99"},
    )
    evidence = EvidenceItem(
        evidence_type=EvidenceType.EMAIL,
        content="Alice confirmed meeting at 3 PM on Friday.",
        source=source,
        confidence=0.98,
    )

    assert evidence.id is not None
    assert evidence.evidence_type == EvidenceType.EMAIL
    assert evidence.source.source_type == "gmail_message"
    assert evidence.source.source_id == "msg_12345"
    assert evidence.source.metadata["sender"] == "alice@example.com"
    assert evidence.confidence == 0.98
    assert evidence.extracted_at is not None


def test_evidence_item_confidence_validation() -> None:
    """Verify confidence boundary enforcement."""
    source = EvidenceSource(source_type="test", source_id="1")
    with pytest.raises(ValidationError):
        EvidenceItem(
            evidence_type=EvidenceType.SYSTEM_FACT,
            content="test fact",
            source=source,
            confidence=1.1,
        )


def test_evidence_json_roundtrip() -> None:
    """Verify serialization and deserialization preserves source identity."""
    source = EvidenceSource(
        source_type="drive_file",
        source_id="file_abc",
        uri="https://drive.google.com/file/d/file_abc",
        title="Q3 Roadmap.pdf",
    )
    item = EvidenceItem(
        evidence_type=EvidenceType.DOCUMENT_CHUNK,
        content="Q3 deliverables include agent runtime v1.",
        source=source,
        confidence=0.92,
    )
    serialized = item.model_dump_json()
    deserialized = EvidenceItem.model_validate_json(serialized)
    assert deserialized.id == item.id
    assert deserialized.source.source_id == "file_abc"
    assert deserialized.source.title == "Q3 Roadmap.pdf"


def test_evidence_item_naive_timestamp_rejected() -> None:
    """Verify EvidenceItem rejects timezone-naive timestamps."""
    import datetime as dt

    source = EvidenceSource(source_type="test", source_id="1")
    naive_dt = dt.datetime(2026, 1, 1, 12, 0, 0)
    with pytest.raises(ValidationError, match="must be UTC-aware"):
        EvidenceItem(
            evidence_type=EvidenceType.SYSTEM_FACT,
            content="fact",
            source=source,
            extracted_at=naive_dt,
        )
