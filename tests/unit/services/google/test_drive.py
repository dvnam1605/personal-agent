"""Unit tests for DriveService domain service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.google.drive import (
    DriveDownloadResult,
    DriveFile,
    DriveFilePage,
    DriveMutationResult,
    DrivePermission,
    DrivePermissionResult,
    DriveUploadRequest,
)
from app.integrations.google_drive import (
    DRIVE_READONLY_SCOPE,
    DRIVE_SCOPE,
    DriveAdapter,
)
from app.services.google.auth import GoogleApiClient, GoogleOAuthService
from app.services.google.drive import DriveService, GoogleDriveService


def _client() -> GoogleApiClient:
    return GoogleApiClient(
        _access_token="test-token",
        base_url="https://google.test",
    )


def _file_mock(file_id: str = "f1", name: str = "doc.txt") -> DriveFile:
    return DriveFile(id=file_id, name=name, mime_type="text/plain")


@pytest.mark.asyncio
async def test_drive_service_from_client_and_alias() -> None:
    client = _client()
    service = DriveService.from_client(client)
    assert isinstance(service, DriveService)
    assert isinstance(service.drive, DriveAdapter)
    assert GoogleDriveService is DriveService


@pytest.mark.asyncio
async def test_drive_service_for_user_creates_client_with_least_scope() -> None:
    mock_oauth = MagicMock(spec=GoogleOAuthService)
    mock_session = AsyncMock()
    mock_client = _client()
    mock_oauth.create_client = AsyncMock(return_value=mock_client)

    service = await DriveService.for_user(
        mock_oauth,
        mock_session,
        "user-123",
    )
    assert isinstance(service, DriveService)
    mock_oauth.create_client.assert_called_once_with(
        mock_session,
        "user-123",
        required_scopes=[DRIVE_READONLY_SCOPE],
        transport=None,
    )

    # Custom scopes
    mock_oauth.create_client.reset_mock()
    service_custom = await DriveService.for_user(
        mock_oauth,
        mock_session,
        "user-123",
        required_scopes=[DRIVE_SCOPE],
    )
    assert isinstance(service_custom, DriveService)
    mock_oauth.create_client.assert_called_once_with(
        mock_session,
        "user-123",
        required_scopes=[DRIVE_SCOPE],
        transport=None,
    )


@pytest.mark.asyncio
async def test_drive_service_delegations() -> None:
    mock_adapter = MagicMock(spec=DriveAdapter)
    mock_adapter.search_files = AsyncMock(return_value=DriveFilePage(items=[_file_mock()]))
    mock_adapter.list_folder = AsyncMock(return_value=DriveFilePage(items=[_file_mock()]))
    mock_adapter.get_metadata = AsyncMock(return_value=_file_mock("f-meta"))
    mock_adapter.download_file = AsyncMock(
        return_value=DriveDownloadResult(
            file_id="f-dl",
            name="file.txt",
            mime_type="text/plain",
            original_mime_type="text/plain",
            size_bytes=4,
            is_exported=False,
            content_bytes=b"test",
            text_content="test",
        )
    )
    mock_adapter.upload_file = AsyncMock(return_value=_file_mock("f-up"))
    mock_adapter.create_folder = AsyncMock(return_value=_file_mock("folder-1"))
    mock_adapter.move_file = AsyncMock(return_value=_file_mock("f-mv"))
    mock_adapter.rename_file = AsyncMock(return_value=_file_mock("f-rn"))
    mock_adapter.delete_file = AsyncMock(
        return_value=DriveMutationResult(resource_id="f-del", operation="delete", deleted=True)
    )
    mock_adapter.update_permissions = AsyncMock(
        return_value=DrivePermissionResult(
            file_id="f-perm",
            permission=DrivePermission(id="p1", role="reader", type="user"),
            operation="create",
        )
    )

    service = DriveService(mock_adapter)

    page = await service.search_files(query="test")
    assert len(page.items) == 1
    assert mock_adapter.search_files.call_args.kwargs["query"] == "test"

    folder_page = await service.list_folder(folder_id="root")
    assert len(folder_page.items) == 1
    assert mock_adapter.list_folder.call_args.kwargs["folder_id"] == "root"

    meta = await service.get_metadata("f-meta")
    assert meta.id == "f-meta"
    assert mock_adapter.get_metadata.call_args.kwargs["file_id"] == "f-meta"

    dl = await service.download_file("f-dl")
    assert dl.file_id == "f-dl"
    assert mock_adapter.download_file.call_args.kwargs["file_id"] == "f-dl"

    req = DriveUploadRequest(name="file.txt", content="hello")
    up = await service.upload_file(req)
    assert up.id == "f-up"
    assert mock_adapter.upload_file.call_args.kwargs["request"] is req

    fld = await service.create_folder("New Folder")
    assert fld.id == "folder-1"
    assert mock_adapter.create_folder.call_args.kwargs["name"] == "New Folder"

    mv = await service.move_file("f-mv", destination_folder_id="dest")
    assert mv.id == "f-mv"
    assert mock_adapter.move_file.call_args.kwargs["file_id"] == "f-mv"
    assert mock_adapter.move_file.call_args.kwargs["destination_folder_id"] == "dest"

    rn = await service.rename_file("f-rn", "new_name.txt")
    assert rn.id == "f-rn"
    assert mock_adapter.rename_file.call_args.kwargs["file_id"] == "f-rn"
    assert mock_adapter.rename_file.call_args.kwargs["new_name"] == "new_name.txt"

    del_res = await service.delete_file("f-del", permanent=True)
    assert del_res.deleted is True
    assert mock_adapter.delete_file.call_args.kwargs["file_id"] == "f-del"
    assert mock_adapter.delete_file.call_args.kwargs["permanent"] is True

    perm_res = await service.update_permissions("f-perm", role="reader", type="user")
    assert perm_res.file_id == "f-perm"
    assert mock_adapter.update_permissions.call_args.kwargs["file_id"] == "f-perm"
    assert mock_adapter.update_permissions.call_args.kwargs["role"] == "reader"
