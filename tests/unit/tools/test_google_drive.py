"""Unit tests for Google Drive tool wrappers and registry declarations."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.models import (
    DriveDownloadResult,
    DriveFile,
    DriveFilePage,
    DriveMutationResult,
    DrivePermission,
    DrivePermissionResult,
    ToolContext,
    ToolInput,
)
from app.integrations.google_drive import DRIVE_READONLY_SCOPE, DRIVE_SCOPE
from app.services.drive import DriveService
from app.tools.google_drive import (
    GoogleDriveTools,
    build_drive_tool_registry,
    drive_tool_definitions,
)


def _mock_service() -> DriveService:
    service = MagicMock(spec=DriveService)
    service.drive = MagicMock()
    service.drive.last_operation_retry_count = 0
    service.drive.begin_operation = MagicMock()
    service.drive.finish_operation = MagicMock()

    service.search_files = AsyncMock(
        return_value=DriveFilePage(
            items=[DriveFile(id="f1", name="search_res.txt", mime_type="text/plain")]
        )
    )
    service.list_folder = AsyncMock(
        return_value=DriveFilePage(
            items=[DriveFile(id="f2", name="folder_item.txt", mime_type="text/plain")]
        )
    )
    service.get_metadata = AsyncMock(
        return_value=DriveFile(id="f3", name="meta.txt", mime_type="text/plain")
    )
    service.download_file = AsyncMock(
        return_value=DriveDownloadResult(
            file_id="f4",
            name="download.txt",
            mime_type="text/plain",
            original_mime_type="text/plain",
            size_bytes=10,
            content_bytes=b"0123456789",
            text_content="0123456789",
        )
    )
    service.upload_file = AsyncMock(
        return_value=DriveFile(id="f5", name="upload.txt", mime_type="text/plain")
    )
    service.create_folder = AsyncMock(
        return_value=DriveFile(
            id="f6", name="NewFolder", mime_type="application/vnd.google-apps.folder"
        )
    )
    service.move_file = AsyncMock(
        return_value=DriveFile(id="f7", name="moved.txt", mime_type="text/plain", parents=["new-p"])
    )
    service.rename_file = AsyncMock(
        return_value=DriveFile(id="f8", name="renamed.txt", mime_type="text/plain")
    )
    service.delete_file = AsyncMock(
        return_value=DriveMutationResult(resource_id="f9", operation="trash", trashed=True)
    )
    service.update_permissions = AsyncMock(
        return_value=DrivePermissionResult(
            file_id="f10",
            permission=DrivePermission(
                id="p1", role="writer", type="user", email_address="test@example.com"
            ),
            operation="create",
        )
    )
    return service


def test_drive_tool_definitions_integrity() -> None:
    definitions = drive_tool_definitions()
    assert len(definitions) == 10

    names = {d.name for d in definitions}
    expected_names = {
        "drive.search_files",
        "drive.list_folder",
        "drive.get_metadata",
        "drive.download_file",
        "drive.upload_file",
        "drive.create_folder",
        "drive.move_file",
        "drive.rename_file",
        "drive.delete_file",
        "drive.update_permissions",
    }
    assert names == expected_names

    for definition in definitions:
        assert definition.category == "drive"
        # Verify supports_all_drives parameter is exposed in every tool schema
        props = definition.parameters_schema.get("properties", {})
        assert "supports_all_drives" in props

        if definition.is_mutation:
            assert definition.action_class in {
                ActionClass.SAFE_WRITE,
                ActionClass.DESTRUCTIVE,
                ActionClass.PERMISSION_CHANGE,
            }
            assert DRIVE_SCOPE in definition.required_scopes
        else:
            assert definition.action_class == ActionClass.READ
            assert definition.risk_level == ActionRiskLevel.READ_ONLY
            assert DRIVE_READONLY_SCOPE in definition.required_scopes


def test_build_drive_tool_registry() -> None:
    registry = build_drive_tool_registry()
    assert len(registry) == 10
    assert "drive.search_files" in registry
    assert "drive.delete_file" in registry

    ro_view = registry.as_read_only()
    assert len(ro_view) == 4
    assert "drive.search_files" in ro_view
    assert "drive.list_folder" in ro_view
    assert "drive.get_metadata" in ro_view
    assert "drive.download_file" in ro_view
    assert "drive.upload_file" not in ro_view
    assert "drive.delete_file" not in ro_view


@pytest.mark.asyncio
async def test_google_drive_tools_read_only_rejection() -> None:
    service = _mock_service()
    tools = GoogleDriveTools(service)
    ro_context = ToolContext(run_id="run-1", user_id="u1", read_only_view=True)

    # Calling a read-only tool works
    res_search = await tools.execute(
        ToolInput(tool_name="drive.search_files", arguments={"query": "test"}),
        ro_context,
    )
    assert res_search.success is True

    # Calling a mutation tool in read-only context fails cleanly with permission denied
    res_upload = await tools.execute(
        ToolInput(
            tool_name="drive.upload_file",
            arguments={"name": "file.txt", "content": "content"},
        ),
        ro_context,
    )
    assert res_upload.success is False
    assert "Read-only tool views cannot execute mutations" in (res_upload.error or "")


@pytest.mark.asyncio
async def test_google_drive_tools_execution_dispatch() -> None:
    service = _mock_service()
    tools = GoogleDriveTools(service)
    context = ToolContext(
        run_id="run-1",
        user_id="u1",
        read_only_view=False,
        approval_token="test-approved",
    )

    # 1. search_files
    res = await tools.execute(
        ToolInput(tool_name="drive.search_files", arguments={"query": "hello"}),
        context,
    )
    assert res.success is True
    assert isinstance(res.output, DriveFilePage)

    # 2. list_folder
    res = await tools.execute(
        ToolInput(tool_name="drive.list_folder", arguments={"folder_id": "root"}),
        context,
    )
    assert res.success is True

    # 3. get_metadata
    res = await tools.execute(
        ToolInput(tool_name="drive.get_metadata", arguments={"file_id": "f3"}),
        context,
    )
    assert res.success is True

    # 4. download_file
    res = await tools.execute(
        ToolInput(tool_name="drive.download_file", arguments={"file_id": "f4"}),
        context,
    )
    assert res.success is True

    # 5. upload_file
    res = await tools.execute(
        ToolInput(
            tool_name="drive.upload_file",
            arguments={"name": "new.txt", "content": "hello world"},
        ),
        context,
    )
    assert res.success is True

    # 6. create_folder
    res = await tools.execute(
        ToolInput(tool_name="drive.create_folder", arguments={"name": "Folder 1"}),
        context,
    )
    assert res.success is True

    # 7. move_file
    res = await tools.execute(
        ToolInput(
            tool_name="drive.move_file",
            arguments={"file_id": "f7", "destination_folder_id": "new-parent"},
        ),
        context,
    )
    assert res.success is True

    # 8. rename_file
    res = await tools.execute(
        ToolInput(
            tool_name="drive.rename_file",
            arguments={"file_id": "f8", "new_name": "renamed.txt"},
        ),
        context,
    )
    assert res.success is True

    # 9. delete_file
    res = await tools.execute(
        ToolInput(
            tool_name="drive.delete_file",
            arguments={"file_id": "f9", "permanent": False},
        ),
        context,
    )
    assert res.success is True

    # 10. update_permissions
    res = await tools.execute(
        ToolInput(
            tool_name="drive.update_permissions",
            arguments={"file_id": "f10", "role": "writer", "email_address": "test@example.com"},
        ),
        context,
    )
    assert res.success is True


@pytest.mark.asyncio
async def test_google_drive_tools_unregistered_tool() -> None:
    service = _mock_service()
    tools = GoogleDriveTools(service)
    context = ToolContext(run_id="run-1", user_id="u1", read_only_view=False)

    res = await tools.execute(
        ToolInput(tool_name="drive.unknown_tool", arguments={}),
        context,
    )
    assert res.success is False
    assert "not registered" in (res.error or "")


@pytest.mark.asyncio
async def test_google_drive_tools_missing_arguments_and_generic_exception() -> None:
    service = _mock_service()
    tools = GoogleDriveTools(service)
    context = ToolContext(run_id="run-1", user_id="u1", read_only_view=False)

    # Missing file_id
    res_no_id = await tools.execute(
        ToolInput(tool_name="drive.get_metadata", arguments={}),
        context,
    )
    assert res_no_id.success is False
    assert "file_id is required" in (res_no_id.error or "")

    # Unexpected internal exception wrapping
    cast(Any, service).get_metadata = AsyncMock(
        side_effect=RuntimeError("Unexpected provider socket failure")
    )
    res_exc = await tools.execute(
        ToolInput(tool_name="drive.get_metadata", arguments={"file_id": "f3"}),
        context,
    )
    assert res_exc.success is False
    assert "Unexpected provider socket failure" in (res_exc.error or "")


@pytest.mark.asyncio
async def test_google_drive_mutation_fails_without_approval_token() -> None:
    service = _mock_service()
    tools = GoogleDriveTools(service)
    context = ToolContext(run_id="run-1", user_id="u1", read_only_view=False)

    res = await tools.execute(
        ToolInput(
            tool_name="drive.upload_file",
            arguments={"name": "new.txt", "content": "hello world"},
        ),
        context,
    )
    assert res.success is False
    assert "requires human approval verification" in (res.error or "")
