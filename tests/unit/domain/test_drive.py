"""Unit tests for Google Drive domain models and contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.domain.models.google.drive import (
    GOOGLE_DOC_MIMETYPE,
    GOOGLE_DRAWING_MIMETYPE,
    GOOGLE_FOLDER_MIMETYPE,
    GOOGLE_FORM_MIMETYPE,
    GOOGLE_MAP_MIMETYPE,
    GOOGLE_NON_EXPORTABLE_MIME_TYPES,
    GOOGLE_SHEET_MIMETYPE,
    GOOGLE_SHORTCUT_MIMETYPE,
    GOOGLE_SITE_MIMETYPE,
    GOOGLE_SLIDES_MIMETYPE,
    DriveDownloadResult,
    DriveFile,
    DriveFilePage,
    DriveMutationResult,
    DrivePermission,
    DrivePermissionResult,
    DriveUploadRequest,
    canonicalize_role,
    get_default_export_mime_type,
    is_exportable_google_native,
    is_folder,
    is_shortcut,
    normalize_datetime,
    parse_rfc3339_timestamp,
)


def test_drive_file_normalization_and_properties() -> None:
    now = datetime.now(UTC)
    file_model = DriveFile(
        id="file-123",
        name="Project Proposal.pdf",
        mime_type="application/pdf",
        description="A proposal document",
        starred=True,
        trashed=False,
        parents=["folder-abc"],
        properties={"department": "engineering"},
        app_properties={"custom_tag": "v1"},
        spaces=["drive"],
        version="42",
        web_content_link="https://drive.google.com/content/123",
        web_view_link="https://drive.google.com/view/123",
        created_at=now,
        modified_at=now,
        size_bytes=1048576,
        quota_bytes_used=1048576,
        md5_checksum="d41d8cd98f00b204e9800998ecf8427e",
        sha256_checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        original_filename="Proposal.pdf",
        file_extension="pdf",
    )

    assert file_model.id == "file-123"
    assert file_model.name == "Project Proposal.pdf"
    assert file_model.is_folder is False
    assert file_model.is_shortcut is False
    assert file_model.is_google_native is False
    assert file_model.size_bytes == 1048576
    assert file_model.md5_checksum == "d41d8cd98f00b204e9800998ecf8427e"


def test_drive_folder_and_shortcut_identification() -> None:
    folder = DriveFile(
        id="folder-root",
        name="My Documents",
        mime_type=GOOGLE_FOLDER_MIMETYPE,
    )
    assert folder.is_folder is True
    assert folder.is_shortcut is False
    assert folder.is_google_native is False
    assert is_folder(GOOGLE_FOLDER_MIMETYPE) is True
    assert is_folder("application/pdf") is False

    shortcut = DriveFile(
        id="shortcut-1",
        name="Shortcut to Doc",
        mime_type=GOOGLE_SHORTCUT_MIMETYPE,
    )
    assert shortcut.is_folder is False
    assert shortcut.is_shortcut is True
    assert shortcut.is_google_native is False
    assert is_shortcut(GOOGLE_SHORTCUT_MIMETYPE) is True
    assert is_shortcut("application/pdf") is False


def test_drive_google_native_document_detection_and_export_types() -> None:
    doc = DriveFile(
        id="doc-123",
        name="Notes",
        mime_type=GOOGLE_DOC_MIMETYPE,
    )
    assert doc.is_folder is False
    assert doc.is_shortcut is False
    assert doc.is_google_native is True

    # Exportable native docs
    assert is_exportable_google_native(GOOGLE_DOC_MIMETYPE) is True
    assert is_exportable_google_native(GOOGLE_SHEET_MIMETYPE) is True
    assert is_exportable_google_native(GOOGLE_SLIDES_MIMETYPE) is True
    assert is_exportable_google_native(GOOGLE_DRAWING_MIMETYPE) is True
    assert is_exportable_google_native("application/pdf") is False

    # Non-exportable native types
    assert GOOGLE_FORM_MIMETYPE in GOOGLE_NON_EXPORTABLE_MIME_TYPES
    assert GOOGLE_SITE_MIMETYPE in GOOGLE_NON_EXPORTABLE_MIME_TYPES
    assert GOOGLE_MAP_MIMETYPE in GOOGLE_NON_EXPORTABLE_MIME_TYPES
    assert is_exportable_google_native(GOOGLE_FORM_MIMETYPE) is False
    assert is_exportable_google_native(GOOGLE_SITE_MIMETYPE) is False

    # Default export mime types
    assert get_default_export_mime_type(GOOGLE_DOC_MIMETYPE) == "text/plain"
    assert get_default_export_mime_type(GOOGLE_SHEET_MIMETYPE) == "text/csv"
    assert get_default_export_mime_type(GOOGLE_SLIDES_MIMETYPE) == "application/pdf"
    assert get_default_export_mime_type(GOOGLE_DRAWING_MIMETYPE) == "image/png"
    assert get_default_export_mime_type("application/pdf") is None
    assert get_default_export_mime_type(GOOGLE_FORM_MIMETYPE) is None


def test_drive_file_validation_rejects_blank_identifiers() -> None:
    with pytest.raises(PydanticValidationError):
        DriveFile(id="", name="file.txt", mime_type="text/plain")

    with pytest.raises(PydanticValidationError):
        DriveFile(id="123", name="   ", mime_type="text/plain")


def test_drive_file_page_pagination() -> None:
    page = DriveFilePage(
        items=[
            DriveFile(id="f1", name="file1.txt", mime_type="text/plain"),
            DriveFile(id="f2", name="file2.txt", mime_type="text/plain"),
        ],
        next_page_token="token-next-page",
        incomplete_search=False,
    )
    assert len(page.items) == 2
    assert page.next_page_token == "token-next-page"
    assert page.incomplete_search is False


def test_drive_download_result() -> None:
    content = b"Hello Google Drive"
    res = DriveDownloadResult(
        file_id="f-download-1",
        name="hello.txt",
        mime_type="text/plain",
        original_mime_type="text/plain",
        size_bytes=len(content),
        is_exported=False,
        content_bytes=content,
        text_content="Hello Google Drive",
        md5_checksum="checksum-123",
        version="1",
    )
    assert res.file_id == "f-download-1"
    assert res.size_bytes == 18
    assert res.text_content == "Hello Google Drive"
    assert res.content_bytes == content
    assert res.is_exported is False
    assert "content_bytes" not in res.model_dump()


def test_drive_upload_request_raw_bytes_conversion() -> None:
    req_str = DriveUploadRequest(
        name="test.txt",
        content="string content",
        mime_type="text/plain",
    )
    assert req_str.raw_bytes == b"string content"

    req_bytes = DriveUploadRequest(
        name="test.bin",
        content=b"\x00\x01\x02",
        mime_type="application/octet-stream",
    )
    assert req_bytes.raw_bytes == b"\x00\x01\x02"

    with pytest.raises(PydanticValidationError):
        DriveUploadRequest(name="  ", content="content")


def test_drive_permission_validation_and_role_casing() -> None:
    perm_reader = DrivePermission(
        id="perm-1",
        role="reader",
        type="user",
        email_address="alice@example.com",
        display_name="Alice",
    )
    assert perm_reader.role == "reader"
    assert perm_reader.type == "user"

    # Canonical role casing for fileOrganizer
    perm_organizer = DrivePermission(
        id="perm-2",
        role="fileorganizer",
        type="user",
        email_address="bob@example.com",
    )
    assert perm_organizer.role == "fileOrganizer"

    assert canonicalize_role("fileorganizer") == "fileOrganizer"
    assert canonicalize_role("FILEORGANIZER") == "fileOrganizer"
    assert canonicalize_role("writer") == "writer"

    # Invalid role
    with pytest.raises(ValueError):
        canonicalize_role("superadmin")

    with pytest.raises(PydanticValidationError):
        DrivePermission(id="perm-1", role="superadmin", type="user")

    # Invalid type
    with pytest.raises(PydanticValidationError):
        DrivePermission(id="perm-1", role="writer", type="aliens")


def test_drive_permission_result_and_mutation_result() -> None:
    perm_res = DrivePermissionResult(
        file_id="f1",
        permission=DrivePermission(
            id="p1", role="writer", type="user", email_address="bob@example.com"
        ),
        operation="create",
        deleted=False,
    )
    assert perm_res.file_id == "f1"
    assert perm_res.operation == "create"
    assert perm_res.permission is not None
    assert perm_res.permission.email_address == "bob@example.com"

    mut_res = DriveMutationResult(
        resource_id="f1",
        operation="trash",
        deleted=False,
        trashed=True,
    )
    assert mut_res.resource_id == "f1"
    assert mut_res.trashed is True
    assert mut_res.deleted is False


def test_timestamp_parsers() -> None:
    assert normalize_datetime(None) is None
    dt_naive = datetime(2026, 8, 20, 10, 0, 0)
    dt_aware = normalize_datetime(dt_naive)
    assert dt_aware is not None
    assert dt_aware.tzinfo == UTC

    ts = parse_rfc3339_timestamp("2026-08-20T10:00:00Z")
    assert ts is not None
    assert ts.year == 2026
    assert ts.hour == 10

    assert parse_rfc3339_timestamp("invalid-date") is None
    assert parse_rfc3339_timestamp(None) is None
