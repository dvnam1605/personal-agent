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
from app.services.approvals import generate_approval_token
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
        return_value=DriveFile(id="f3", name="meta.txt", mime_type="text/plain", version="v1")
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
            if definition.name in {
                "drive.move_file",
                "drive.rename_file",
                "drive.delete_file",
                "drive.update_permissions",
            }:
                assert "expected_version" in props
            else:
                assert "expected_version" not in props
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

    async def _run(tool_name: str, arguments: dict[str, Any], *, mutation: bool = False):
        token = None
        if mutation:
            token = generate_approval_token(
                approval_id=f"drive-{tool_name}",
                tool_name=tool_name,
                run_id="run-1",
                arguments=arguments,
            )
        context = ToolContext(
            run_id="run-1",
            user_id="u1",
            read_only_view=False,
            approval_token=token,
        )
        return await tools.execute(ToolInput(tool_name=tool_name, arguments=arguments), context)

    # 1. search_files
    res = await _run("drive.search_files", {"query": "hello"})
    assert res.success is True
    assert isinstance(res.output, DriveFilePage)

    # 2. list_folder
    res = await _run("drive.list_folder", {"folder_id": "root"})
    assert res.success is True

    # 3. get_metadata
    res = await _run("drive.get_metadata", {"file_id": "f3"})
    assert res.success is True

    # 4. download_file
    res = await _run("drive.download_file", {"file_id": "f4"})
    assert res.success is True

    # 5. upload_file
    res = await _run(
        "drive.upload_file",
        {"name": "new.txt", "content": "hello world"},
        mutation=True,
    )
    assert res.success is True

    # 6. create_folder
    res = await _run("drive.create_folder", {"name": "Folder 1"}, mutation=True)
    assert res.success is True

    # 7. move_file
    res = await _run(
        "drive.move_file",
        {"file_id": "f7", "destination_folder_id": "new-parent", "expected_version": "v1"},
        mutation=True,
    )
    assert res.success is True

    # 8. rename_file
    res = await _run(
        "drive.rename_file",
        {"file_id": "f8", "new_name": "renamed.txt", "expected_version": "v1"},
        mutation=True,
    )
    assert res.success is True

    # 9. delete_file
    res = await _run(
        "drive.delete_file",
        {"file_id": "f9", "permanent": False, "expected_version": "v1"},
        mutation=True,
    )
    assert res.success is True

    # 10. update_permissions
    res = await _run(
        "drive.update_permissions",
        {
            "file_id": "f10",
            "role": "writer",
            "email_address": "test@example.com",
            "expected_version": "v1",
        },
        mutation=True,
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
    assert "Drive tool execution failed: RuntimeError" in (res_exc.error or "")
    assert "Unexpected provider socket failure" not in (res_exc.error or "")


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


@pytest.mark.asyncio
async def test_google_drive_mutation_fails_with_spoofed_or_expired_token() -> None:
    service = _mock_service()
    tools = GoogleDriveTools(service)
    # 1. Arbitrary spoofed token fails closed (H1)
    context_spoofed = ToolContext(
        run_id="run-1", user_id="u1", read_only_view=False, approval_token="test-approved"
    )
    res_spoofed = await tools.execute(
        ToolInput(
            tool_name="drive.upload_file",
            arguments={"name": "new.txt", "content": "hello world"},
        ),
        context_spoofed,
    )
    assert res_spoofed.success is False
    assert "rejected: approval token is invalid, expired, or unverified" in (
        res_spoofed.error or ""
    )

    # 2. Token issued for a different tool fails closed
    wrong_tool_token = generate_approval_token(
        approval_id="drive-req-wrong", tool_name="drive.rename_file"
    )
    context_wrong = ToolContext(
        run_id="run-1", user_id="u1", read_only_view=False, approval_token=wrong_tool_token
    )
    res_wrong = await tools.execute(
        ToolInput(
            tool_name="drive.upload_file",
            arguments={"name": "new.txt", "content": "hello world"},
        ),
        context_wrong,
    )
    assert res_wrong.success is False
    assert "rejected: approval token is invalid, expired, or unverified" in (res_wrong.error or "")


@pytest.mark.asyncio
async def test_drive_delete_rejects_stale_live_version() -> None:
    """When expected_version is set, live metadata.version is curr_fp (M3)."""
    service = _mock_service()
    service.get_metadata = AsyncMock(
        return_value=DriveFile(id="f9", name="gone.txt", mime_type="text/plain", version="v2")
    )
    tools = GoogleDriveTools(service)
    args = {"file_id": "f9", "permanent": False, "expected_version": "v1"}
    token = generate_approval_token(
        approval_id="drive-stale-ver",
        tool_name="drive.delete_file",
        run_id="run-1",
        arguments=args,
    )
    res = await tools.execute(
        ToolInput(tool_name="drive.delete_file", arguments=args),
        ToolContext(run_id="run-1", user_id="u1", approval_token=token),
    )
    assert res.success is False
    assert "stale target state" in (res.error or "")
    cast(Any, service.delete_file).assert_not_called()
