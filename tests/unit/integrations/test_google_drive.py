"""Deterministic P8 tests for Google Drive adapter operations."""

from typing import Any

import httpx
import pytest

from app.domain.errors import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from app.domain.errors import (
    ValidationError as DomainValidationError,
)
from app.domain.models.drive import (
    GOOGLE_DOC_MIMETYPE,
    GOOGLE_FOLDER_MIMETYPE,
    GOOGLE_FORM_MIMETYPE,
    GOOGLE_SHORTCUT_MIMETYPE,
    DriveUploadRequest,
)
from app.integrations.google_common import RetryPolicy
from app.integrations.google_drive import (
    DriveAdapter,
    _validate_identifier,
    _validate_page_size,
)
from app.services.google_auth import GoogleApiClient


class FakeGoogleTransport:
    """Queue-based authorized transport; no request reaches Google."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _next(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(
                f"Fake provider response queue is empty when handling {method} {url}."
            )
        return self.responses.pop(0)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("DELETE", url, **kwargs)


def _client(transport: FakeGoogleTransport) -> GoogleApiClient:
    return GoogleApiClient(
        _access_token="test-access-token",
        base_url="https://google.test",
        transport=transport,
    )


def _json_response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _file_payload(
    file_id: str = "file-1",
    name: str = "document.pdf",
    mime_type: str = "application/pdf",
    size: int = 12345,
    parents: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": file_id,
        "name": name,
        "mimeType": mime_type,
        "size": str(size),
        "parents": parents or ["root"],
        "createdTime": "2026-08-20T08:00:00Z",
        "modifiedTime": "2026-08-20T09:00:00Z",
        "md5Checksum": "d41d8cd98f00b204e9800998ecf8427e",
        "version": "1",
        "starred": False,
        "trashed": False,
    }


def test_validate_identifier_and_page_size_edge_cases() -> None:
    # Valid identifier
    assert _validate_identifier("valid-id_123", "id") == "valid-id_123"

    # Reject / \ ? # ' " ..
    for invalid in [
        "folder/path",
        "folder\\path",
        "id?query",
        "id#frag",
        "../parent",
        "   ",
        "",
        "id' or 'a'='a",  # Drive q single-quote injection
        'id"quote',
    ]:
        with pytest.raises(DomainValidationError, match="Invalid Google Drive"):
            _validate_identifier(invalid, "test")

    # Page size bounds
    assert _validate_page_size(1) == 1
    assert _validate_page_size(1000) == 1000
    with pytest.raises(DomainValidationError):
        _validate_page_size(0)
    with pytest.raises(DomainValidationError):
        _validate_page_size(1001)


@pytest.mark.asyncio
async def test_drive_search_files() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(
                200,
                {
                    "files": [
                        _file_payload(file_id="f1", name="Report.pdf"),
                        _file_payload(file_id="f2", name="Summary.docx"),
                    ],
                    "nextPageToken": "token-page-2",
                    "incompleteSearch": False,
                },
            )
        ]
    )
    adapter = DriveAdapter(_client(transport))
    page = await adapter.search_files(query="name contains 'Report'", page_size=20)

    assert len(page.items) == 2
    assert page.items[0].id == "f1"
    assert page.items[0].name == "Report.pdf"
    assert page.next_page_token == "token-page-2"
    assert page.incomplete_search is False

    assert len(transport.calls) == 1
    method, _, kwargs = transport.calls[0]
    assert method == "GET"
    assert "pageSize" in kwargs["params"]
    assert kwargs["params"]["pageSize"] == 20
    assert kwargs["params"]["q"] == "trashed = false and (name contains 'Report')"


@pytest.mark.asyncio
async def test_drive_list_folder() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(
                200,
                {
                    "files": [
                        _file_payload(
                            file_id="folder-child",
                            name="Subfolder",
                            mime_type=GOOGLE_FOLDER_MIMETYPE,
                        ),
                    ],
                    "nextPageToken": None,
                },
            )
        ]
    )
    adapter = DriveAdapter(_client(transport))
    page = await adapter.list_folder(folder_id="folder-123", include_trashed=False)

    assert len(page.items) == 1
    assert page.items[0].is_folder is True
    assert transport.calls[0][2]["params"]["q"] == "trashed = false and 'folder-123' in parents"


@pytest.mark.asyncio
async def test_drive_get_metadata() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(200, _file_payload(file_id="f-meta", name="Quarterly.pdf", size=50000)),
        ]
    )
    adapter = DriveAdapter(_client(transport))
    metadata = await adapter.get_metadata("f-meta")

    assert metadata.id == "f-meta"
    assert metadata.name == "Quarterly.pdf"
    assert metadata.size_bytes == 50000
    assert metadata.md5_checksum == "d41d8cd98f00b204e9800998ecf8427e"


@pytest.mark.asyncio
async def test_drive_download_binary_file() -> None:
    binary_content = b"PDF-1.4 binary content here"
    transport = FakeGoogleTransport(
        [
            # First call: get_metadata
            _json_response(
                200, _file_payload(file_id="f-bin", name="file.pdf", mime_type="application/pdf")
            ),
            # Second call: alt=media download
            httpx.Response(
                200, content=binary_content, headers={"Content-Type": "application/pdf"}
            ),
        ]
    )
    adapter = DriveAdapter(_client(transport))
    res = await adapter.download_file("f-bin")

    assert res.file_id == "f-bin"
    assert res.name == "file.pdf"
    assert res.content_bytes == binary_content
    assert res.size_bytes == len(binary_content)
    assert res.is_exported is False
    assert res.mime_type == "application/pdf"

    assert len(transport.calls) == 2
    assert transport.calls[1][0] == "GET"
    assert transport.calls[1][2]["params"]["alt"] == "media"


@pytest.mark.asyncio
async def test_drive_download_google_doc_export_and_validation() -> None:
    exported_text = "This is exported text from a Google Doc."
    transport = FakeGoogleTransport(
        [
            # get_metadata returns Google Doc mime type
            _json_response(
                200,
                _file_payload(file_id="doc-1", name="Meeting Notes", mime_type=GOOGLE_DOC_MIMETYPE),
            ),
            # export call returns exported text/plain
            httpx.Response(
                200,
                content=exported_text.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            ),
        ]
    )
    adapter = DriveAdapter(_client(transport))
    res = await adapter.download_file("doc-1", export_mime_type="text/plain")

    assert res.file_id == "doc-1"
    assert res.name == "Meeting Notes"
    assert res.is_exported is True
    assert res.mime_type == "text/plain"
    assert res.original_mime_type == GOOGLE_DOC_MIMETYPE
    assert res.text_content == exported_text
    assert res.size_bytes == len(exported_text.encode("utf-8"))

    assert len(transport.calls) == 2
    assert "export" in transport.calls[1][1]
    assert transport.calls[1][2]["params"]["mimeType"] == "text/plain"

    # Unsupported export MIME type rejection
    transport_invalid = FakeGoogleTransport(
        [
            _json_response(
                200,
                _file_payload(file_id="doc-1", name="Meeting Notes", mime_type=GOOGLE_DOC_MIMETYPE),
            ),
        ]
    )
    adapter_invalid = DriveAdapter(_client(transport_invalid))
    with pytest.raises(
        DomainValidationError, match="Export MIME type 'image/png' is not supported"
    ):
        await adapter_invalid.download_file("doc-1", export_mime_type="image/png")


@pytest.mark.asyncio
async def test_drive_download_rejects_folders_shortcuts_and_forms() -> None:
    # 1. Reject Folder
    transport_folder = FakeGoogleTransport(
        [
            _json_response(
                200,
                _file_payload(file_id="folder-1", name="Folder", mime_type=GOOGLE_FOLDER_MIMETYPE),
            ),
        ]
    )
    adapter = DriveAdapter(_client(transport_folder))
    with pytest.raises(DomainValidationError, match="Cannot download folder"):
        await adapter.download_file("folder-1")

    # 2. Reject Shortcut
    transport_shortcut = FakeGoogleTransport(
        [
            _json_response(
                200,
                _file_payload(file_id="sc-1", name="Shortcut", mime_type=GOOGLE_SHORTCUT_MIMETYPE),
            ),
        ]
    )
    adapter = DriveAdapter(_client(transport_shortcut))
    with pytest.raises(DomainValidationError, match="Cannot download shortcut"):
        await adapter.download_file("sc-1")

    # 3. Reject Form / Non-exportable
    transport_form = FakeGoogleTransport(
        [
            _json_response(
                200,
                _file_payload(file_id="form-1", name="Survey", mime_type=GOOGLE_FORM_MIMETYPE),
            ),
        ]
    )
    adapter = DriveAdapter(_client(transport_form))
    with pytest.raises(DomainValidationError, match="does not support file export"):
        await adapter.download_file("form-1")


@pytest.mark.asyncio
async def test_drive_upload_file_dynamic_multipart_boundary() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(
                200, _file_payload(file_id="f-uploaded", name="newfile.txt", mime_type="text/plain")
            ),
        ]
    )
    adapter = DriveAdapter(_client(transport))
    req = DriveUploadRequest(
        name="newfile.txt",
        content="Hello uploaded content with UTF-8: Xin chào",
        mime_type="text/plain",
        parents=["folder-abc"],
        description="A test upload",
    )
    uploaded = await adapter.upload_file(req)

    assert uploaded.id == "f-uploaded"
    assert uploaded.name == "newfile.txt"

    assert len(transport.calls) == 1
    method, url, kwargs = transport.calls[0]
    assert method == "POST"
    assert "uploadType=multipart" in url
    content_type_header = kwargs["headers"]["Content-Type"]
    assert "multipart/related; boundary=" in content_type_header
    assert "antigravity_drive_" in content_type_header
    # Boundary is dynamic
    assert (
        content_type_header
        != "multipart/related; boundary===============antigravity_drive_boundary=="
    )
    assert b"newfile.txt" in kwargs["content"]
    assert "Xin chào".encode() in kwargs["content"]


@pytest.mark.asyncio
async def test_drive_create_folder() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(
                200,
                _file_payload(
                    file_id="folder-new",
                    name="Q3 Budget",
                    mime_type=GOOGLE_FOLDER_MIMETYPE,
                    parents=["parent-123"],
                ),
            )
        ]
    )
    adapter = DriveAdapter(_client(transport))
    folder = await adapter.create_folder("Q3 Budget", parent_folder_id="parent-123")

    assert folder.id == "folder-new"
    assert folder.name == "Q3 Budget"
    assert folder.is_folder is True
    assert folder.parents == ["parent-123"]


@pytest.mark.asyncio
async def test_drive_move_file_validates_source_and_destination() -> None:
    transport = FakeGoogleTransport(
        [
            # First call: get_metadata to discover current parents
            _json_response(200, _file_payload(file_id="f-move", parents=["old-folder"])),
            # Second call: PATCH with addParents and removeParents
            _json_response(200, _file_payload(file_id="f-move", parents=["new-folder"])),
        ]
    )
    adapter = DriveAdapter(_client(transport))
    moved = await adapter.move_file("f-move", destination_folder_id="new-folder")

    assert moved.id == "f-move"
    assert moved.parents == ["new-folder"]
    assert len(transport.calls) == 2
    patch_call = transport.calls[1]
    assert patch_call[0] == "PATCH"
    assert patch_call[2]["params"]["addParents"] == "new-folder"
    assert patch_call[2]["params"]["removeParents"] == "old-folder"

    # Explicit source_folder_id validation
    transport_source = FakeGoogleTransport(
        [
            _json_response(200, _file_payload(file_id="f-move", parents=["new-folder"])),
        ]
    )
    adapter_source = DriveAdapter(_client(transport_source))
    await adapter_source.move_file(
        "f-move", destination_folder_id="new-folder", source_folder_id="custom-src"
    )
    assert transport_source.calls[0][2]["params"]["removeParents"] == "custom-src"

    # Reject invalid source_folder_id with path traversal characters
    with pytest.raises(DomainValidationError, match="Invalid Google Drive source_folder_id"):
        await adapter_source.move_file(
            "f-move", destination_folder_id="new-folder", source_folder_id="../injected"
        )


@pytest.mark.asyncio
async def test_drive_rename_file() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(200, _file_payload(file_id="f-rename", name="Renamed.pdf")),
        ]
    )
    adapter = DriveAdapter(_client(transport))
    renamed = await adapter.rename_file("f-rename", "Renamed.pdf")

    assert renamed.id == "f-rename"
    assert renamed.name == "Renamed.pdf"
    assert transport.calls[0][0] == "PATCH"
    assert transport.calls[0][2]["json"]["name"] == "Renamed.pdf"


@pytest.mark.asyncio
async def test_drive_delete_file_trash_and_permanent() -> None:
    # 1. Trash (reversible)
    transport_trash = FakeGoogleTransport(
        [
            _json_response(200, {"id": "f-trash", "trashed": True}),
        ]
    )
    adapter_trash = DriveAdapter(_client(transport_trash))
    res_trash = await adapter_trash.delete_file("f-trash", permanent=False)
    assert res_trash.resource_id == "f-trash"
    assert res_trash.operation == "trash"
    assert res_trash.trashed is True
    assert res_trash.deleted is False
    assert transport_trash.calls[0][0] == "PATCH"
    assert transport_trash.calls[0][2]["json"]["trashed"] is True

    # 2. Permanent delete
    transport_perm = FakeGoogleTransport(
        [
            httpx.Response(204),
        ]
    )
    adapter_perm = DriveAdapter(_client(transport_perm))
    res_perm = await adapter_perm.delete_file("f-perm", permanent=True)
    assert res_perm.resource_id == "f-perm"
    assert res_perm.operation == "delete"
    assert res_perm.deleted is True
    assert res_perm.trashed is False
    assert transport_perm.calls[0][0] == "DELETE"


@pytest.mark.asyncio
async def test_drive_update_permissions_create_update_delete_and_casing() -> None:
    # 1. Create permission with canonical casing (fileOrganizer)
    transport_create = FakeGoogleTransport(
        [
            _json_response(
                200,
                {
                    "id": "perm-123",
                    "role": "fileOrganizer",
                    "type": "user",
                    "emailAddress": "bob@example.com",
                    "displayName": "Bob",
                },
            )
        ]
    )
    adapter_create = DriveAdapter(_client(transport_create))
    res_create = await adapter_create.update_permissions(
        "file-share",
        role="fileorganizer",
        type="user",
        email_address="bob@example.com",
    )
    assert res_create.file_id == "file-share"
    assert res_create.operation == "create"
    assert res_create.permission is not None
    assert res_create.permission.role == "fileOrganizer"
    assert res_create.permission.email_address == "bob@example.com"
    # Ensure request body sent fileOrganizer
    assert transport_create.calls[0][2]["json"]["role"] == "fileOrganizer"

    # 2. Type coupling validation
    adapter_fail = DriveAdapter(_client(FakeGoogleTransport([])))
    with pytest.raises(DomainValidationError, match="email_address is required"):
        await adapter_fail.update_permissions("file-share", role="reader", type="user")

    with pytest.raises(DomainValidationError, match="domain is required"):
        await adapter_fail.update_permissions("file-share", role="reader", type="domain")

    # 3. Update existing permission
    transport_update = FakeGoogleTransport(
        [
            _json_response(
                200,
                {
                    "id": "perm-123",
                    "role": "reader",
                    "type": "user",
                    "emailAddress": "bob@example.com",
                },
            )
        ]
    )
    adapter_update = DriveAdapter(_client(transport_update))
    res_update = await adapter_update.update_permissions(
        "file-share",
        role="reader",
        permission_id="perm-123",
    )
    assert res_update.operation == "update"
    assert res_update.permission is not None
    assert res_update.permission.role == "reader"

    # 4. Remove permission
    transport_delete = FakeGoogleTransport(
        [
            httpx.Response(204),
        ]
    )
    adapter_delete = DriveAdapter(_client(transport_delete))
    res_delete = await adapter_delete.update_permissions(
        "file-share",
        permission_id="perm-123",
        remove=True,
    )
    assert res_delete.operation == "delete"
    assert res_delete.deleted is True
    assert res_delete.permission is None


@pytest.mark.asyncio
async def test_drive_adapter_error_mappings() -> None:
    # 404
    transport = FakeGoogleTransport([httpx.Response(404)])
    adapter = DriveAdapter(_client(transport))
    with pytest.raises(NotFoundError):
        await adapter.get_metadata("nonexistent")

    # 403
    transport = FakeGoogleTransport([httpx.Response(403)])
    adapter = DriveAdapter(_client(transport))
    with pytest.raises(PermissionDeniedError):
        await adapter.get_metadata("forbidden")

    # 401
    transport = FakeGoogleTransport([httpx.Response(401)])
    adapter = DriveAdapter(_client(transport))
    with pytest.raises(AuthenticationError):
        await adapter.get_metadata("expired")

    # 429
    transport = FakeGoogleTransport([httpx.Response(429)])
    adapter = DriveAdapter(_client(transport), retry_policy=RetryPolicy(max_attempts=1))
    with pytest.raises(RateLimitError):
        await adapter.get_metadata("throttled")


@pytest.mark.asyncio
async def test_drive_adapter_retries_and_telemetry() -> None:
    transport = FakeGoogleTransport(
        [
            httpx.Response(500),
            httpx.Response(503),
            _json_response(200, _file_payload(file_id="f-retry")),
        ]
    )
    adapter = DriveAdapter(
        _client(transport),
        retry_policy=RetryPolicy(
            max_attempts=3, initial_delay_seconds=0.001, max_delay_seconds=0.01
        ),
    )
    adapter.begin_operation()
    res = await adapter.get_metadata("f-retry")
    adapter.finish_operation()

    assert res.id == "f-retry"
    assert adapter.last_operation_retry_count == 2


@pytest.mark.asyncio
async def test_drive_text_content_size_limit() -> None:
    # 2 MB binary text payload
    large_text = b"a" * (2 * 1024 * 1024)
    transport = FakeGoogleTransport(
        [
            _json_response(
                200, _file_payload(file_id="f-large", name="large.txt", mime_type="text/plain")
            ),
            httpx.Response(200, content=large_text, headers={"Content-Type": "text/plain"}),
        ]
    )
    adapter = DriveAdapter(_client(transport))
    res = await adapter.download_file("f-large")
    assert res.size_bytes == 2 * 1024 * 1024
    # text_content is None because file size exceeds 1MB limit
    assert res.text_content is None
