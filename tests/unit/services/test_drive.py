"""Unit tests for DriveService domain service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.drive import (
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
from app.services.drive import DriveService, GoogleDriveService
from app.services.google_auth import GoogleApiClient, GoogleOAuthService


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

    # 1. search_files
    page = await service.search_files(query="test")
    assert len(page.items) == 1
    mock_adapter.search_files.assert_called_once_with(query="test")

    # 2. list_folder
    folder_page = await service.list_folder(folder_id="root")
    assert len(folder_page.items) == 1
    mock_adapter.list_folder.assert_called_once_with(folder_id="root")

    # 3. get_metadata
    meta = await service.get_metadata("f-meta")
    assert meta.id == "f-meta"
    mock_adapter.get_metadata.assert_called_once_with("f-meta")

    # 4. download_file
    dl = await service.download_file("f-dl")
    assert dl.file_id == "f-dl"
    mock_adapter.download_file.assert_called_once_with("f-dl")

    # 5. upload_file
    req = DriveUploadRequest(name="file.txt", content="hello")
    up = await service.upload_file(req)
    assert up.id == "f-up"
    mock_adapter.upload_file.assert_called_once_with(req)

    # 6. create_folder
    fld = await service.create_folder("New Folder")
    assert fld.id == "folder-1"
    mock_adapter.create_folder.assert_called_once_with("New Folder")

    # 7. move_file
    mv = await service.move_file("f-mv", destination_folder_id="dest")
    assert mv.id == "f-mv"
    mock_adapter.move_file.assert_called_once_with("f-mv", destination_folder_id="dest")

    # 8. rename_file
    rn = await service.rename_file("f-rn", "new_name.txt")
    assert rn.id == "f-rn"
    mock_adapter.rename_file.assert_called_once_with("f-rn", "new_name.txt")

    # 9. delete_file
    del_res = await service.delete_file("f-del", permanent=True)
    assert del_res.deleted is True
    mock_adapter.delete_file.assert_called_once_with("f-del", permanent=True)

    # 10. update_permissions
    perm_res = await service.update_permissions("f-perm", role="reader", type="user")
    assert perm_res.file_id == "f-perm"
    mock_adapter.update_permissions.assert_called_once_with("f-perm", role="reader", type="user")
