"""Deterministic Google Drive tool declarations and execution wrappers."""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import structlog

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.errors import AppError, PermissionDeniedError, ValidationError
from app.domain.models import (
    DriveUploadRequest,
    ToolContext,
    ToolDefinition,
    ToolExecutionMetadata,
    ToolInput,
    ToolResult,
)
from app.integrations.google_drive import (
    DRIVE_READONLY_SCOPE,
    DRIVE_SCOPE,
)
from app.services.approvals import (
    STALE_CHECK_VERSION_TOOLS,
    expected_target_fingerprint_from_arguments,
    require_mutation_approval,
)
from app.services.google.drive import DriveService
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


def _tool(
    name: str,
    description: str,
    *,
    capabilities: list[str],
    parameters_schema: dict[str, Any],
    required_scopes: list[str],
    action_class: ActionClass = ActionClass.READ,
    risk_level: ActionRiskLevel = ActionRiskLevel.READ_ONLY,
    is_mutation: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        category="drive",
        capabilities=capabilities,
        parameters_schema=parameters_schema,
        required_scopes=required_scopes,
        action_class=action_class,
        risk_level=risk_level,
        is_mutation=is_mutation,
    )


_DRIVE_READ = [DRIVE_READONLY_SCOPE]
_DRIVE_WRITE = [DRIVE_SCOPE]
_STRING = {"type": "string"}
_BOOLEAN = {"type": "boolean"}
_INTEGER = {"type": "integer"}
_EXPECTED_VERSION = {
    "type": "string",
    "description": (
        "DriveFile.version from the last metadata read. Required for stale-target "
        "protection on move/rename/delete/permissions. Omitting it is rejected "
        "(fail-closed). Also sent to Google as If-Match when it is an ETag."
    ),
}


DRIVE_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    _tool(
        "drive.search_files",
        "Search Google Drive files and folders using query expressions, ordering, and pagination.",
        capabilities=["drive.read", "drive.search"],
        parameters_schema={
            "type": "object",
            "properties": {
                "query": _STRING,
                "folder_id": _STRING,
                "page_size": {"type": "integer", "minimum": 1, "maximum": 1000},
                "page_token": _STRING,
                "order_by": _STRING,
                "spaces": _STRING,
                "include_trashed": _BOOLEAN,
                "supports_all_drives": _BOOLEAN,
                "include_items_from_all_drives": _BOOLEAN,
                "corpora": _STRING,
                "drive_id": _STRING,
            },
        },
        required_scopes=_DRIVE_READ,
    ),
    _tool(
        "drive.list_folder",
        "List files and subfolders within a specific Google Drive folder.",
        capabilities=["drive.read", "drive.files"],
        parameters_schema={
            "type": "object",
            "properties": {
                "folder_id": _STRING,
                "page_size": {"type": "integer", "minimum": 1, "maximum": 1000},
                "page_token": _STRING,
                "order_by": _STRING,
                "include_trashed": _BOOLEAN,
                "supports_all_drives": _BOOLEAN,
            },
        },
        required_scopes=_DRIVE_READ,
    ),
    _tool(
        "drive.get_metadata",
        "Fetch comprehensive metadata for a Google Drive file or folder.",
        capabilities=["drive.read", "drive.files"],
        parameters_schema={
            "type": "object",
            "properties": {
                "file_id": _STRING,
                "supports_all_drives": _BOOLEAN,
            },
            "required": ["file_id"],
        },
        required_scopes=_DRIVE_READ,
    ),
    _tool(
        "drive.download_file",
        "Download binary file content or export Google-native documents to supported formats.",
        capabilities=["drive.read", "drive.files", "drive.download"],
        parameters_schema={
            "type": "object",
            "properties": {
                "file_id": _STRING,
                "export_mime_type": _STRING,
                "acknowledge_abuse": _BOOLEAN,
                "supports_all_drives": _BOOLEAN,
            },
            "required": ["file_id"],
        },
        required_scopes=_DRIVE_READ,
    ),
    _tool(
        "drive.upload_file",
        "Upload a new file to Google Drive with optional parent folder and metadata.",
        capabilities=["drive.write", "drive.files", "drive.upload"],
        parameters_schema={
            "type": "object",
            "properties": {
                "name": _STRING,
                "content": _STRING,
                "mime_type": _STRING,
                "folder_id": _STRING,
                "description": _STRING,
                "starred": _BOOLEAN,
                "supports_all_drives": _BOOLEAN,
            },
            "required": ["name", "content"],
        },
        required_scopes=_DRIVE_WRITE,
        action_class=ActionClass.SAFE_WRITE,
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "drive.create_folder",
        "Create a new folder in Google Drive.",
        capabilities=["drive.write", "drive.files", "drive.folders"],
        parameters_schema={
            "type": "object",
            "properties": {
                "name": _STRING,
                "parent_folder_id": _STRING,
                "description": _STRING,
                "supports_all_drives": _BOOLEAN,
            },
            "required": ["name"],
        },
        required_scopes=_DRIVE_WRITE,
        action_class=ActionClass.SAFE_WRITE,
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "drive.move_file",
        "Move a Google Drive file or folder to a different parent folder.",
        capabilities=["drive.write", "drive.files", "drive.move"],
        parameters_schema={
            "type": "object",
            "properties": {
                "file_id": _STRING,
                "destination_folder_id": _STRING,
                "source_folder_id": _STRING,
                "supports_all_drives": _BOOLEAN,
                "expected_version": _EXPECTED_VERSION,
            },
            "required": ["file_id", "destination_folder_id"],
        },
        required_scopes=_DRIVE_WRITE,
        action_class=ActionClass.SAFE_WRITE,
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "drive.rename_file",
        "Rename a Google Drive file or folder.",
        capabilities=["drive.write", "drive.files", "drive.rename"],
        parameters_schema={
            "type": "object",
            "properties": {
                "file_id": _STRING,
                "new_name": _STRING,
                "supports_all_drives": _BOOLEAN,
                "expected_version": _EXPECTED_VERSION,
            },
            "required": ["file_id", "new_name"],
        },
        required_scopes=_DRIVE_WRITE,
        action_class=ActionClass.SAFE_WRITE,
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "drive.delete_file",
        "Delete or trash a Google Drive file or folder.",
        capabilities=["drive.write", "drive.files", "drive.delete"],
        parameters_schema={
            "type": "object",
            "properties": {
                "file_id": _STRING,
                "permanent": _BOOLEAN,
                "supports_all_drives": _BOOLEAN,
                "expected_version": _EXPECTED_VERSION,
            },
            "required": ["file_id"],
        },
        required_scopes=_DRIVE_WRITE,
        action_class=ActionClass.DESTRUCTIVE,
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        is_mutation=True,
    ),
    _tool(
        "drive.update_permissions",
        "Manage or update sharing permissions on a Google Drive file or folder.",
        capabilities=["drive.write", "drive.permissions", "drive.share"],
        parameters_schema={
            "type": "object",
            "properties": {
                "file_id": _STRING,
                "role": {
                    "type": "string",
                    "enum": [
                        "reader",
                        "commenter",
                        "writer",
                        "organizer",
                        "fileOrganizer",
                        "owner",
                    ],
                },
                "type": {"type": "string", "enum": ["user", "group", "domain", "anyone"]},
                "email_address": _STRING,
                "domain": _STRING,
                "permission_id": _STRING,
                "remove": _BOOLEAN,
                "send_notification_email": _BOOLEAN,
                "email_message": _STRING,
                "transfer_ownership": _BOOLEAN,
                "supports_all_drives": _BOOLEAN,
                "expected_version": _EXPECTED_VERSION,
            },
            "required": ["file_id"],
        },
        required_scopes=_DRIVE_WRITE,
        action_class=ActionClass.PERMISSION_CHANGE,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        is_mutation=True,
    ),
)

_DEFINITIONS_BY_NAME = {definition.name: definition for definition in DRIVE_TOOL_DEFINITIONS}


def drive_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Return defensive copies of every P8 Drive tool declaration."""
    return tuple(definition.model_copy(deep=True) for definition in DRIVE_TOOL_DEFINITIONS)


def build_drive_tool_registry() -> ToolRegistry:
    """Build a registry containing only Drive tools."""
    return ToolRegistry(drive_tool_definitions())


class GoogleDriveTools:
    """Expose typed Drive service calls as normalized ToolResult values."""

    def __init__(self, service: DriveService) -> None:
        self.service = service

    @classmethod
    async def for_user(
        cls,
        oauth_service,
        session: AsyncSession,
        user_id: str,
        *,
        tool_name: str | None = None,
        required_scopes: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> GoogleDriveTools:
        if required_scopes is None and tool_name is not None:
            definition = _DEFINITIONS_BY_NAME.get(tool_name)
            if definition is None:
                raise ValidationError("Drive tool is not registered.")
            required_scopes = definition.required_scopes
        service = await DriveService.for_user(
            oauth_service,
            session,
            user_id,
            required_scopes=required_scopes,
            **kwargs,
        )
        return cls(service)

    async def execute(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Execute a declared Drive operation without an LLM decision point."""
        started = time.perf_counter()
        self.service.drive.begin_operation()
        try:
            definition = _DEFINITIONS_BY_NAME.get(tool_input.tool_name)
            if definition is None:
                return self._failure(
                    tool_input.tool_name,
                    "Drive tool is not registered.",
                    started=started,
                )
            if_match: str | None = None
            if definition.is_mutation:
                if context.read_only_view:
                    raise PermissionDeniedError("Read-only tool views cannot execute mutations.")
                current_fp, if_match = await self._live_precondition(tool_input)
                await require_mutation_approval(
                    tool_name=tool_input.tool_name,
                    context_token=context.approval_token,
                    arguments=tool_input.arguments,
                    run_id=context.run_id,
                    user_id=context.user_id,
                    delegation=context.delegation,
                    consume=True,
                    current_target_fingerprint=current_fp,
                )
            output = await self._dispatch(
                tool_input.tool_name, tool_input.arguments, if_match=if_match
            )
            return ToolResult(
                tool_name=tool_input.tool_name,
                success=True,
                output=output,
                metadata=self._metadata(tool_input.tool_name, started),
            )
        except AppError as exc:
            return self._failure(tool_input.tool_name, exc.message, started=started)
        except Exception as exc:  # noqa: BLE001 - unexpected failures become ToolResult
            logger.exception(
                "Drive tool execution failed with unexpected exception",
                tool_name=tool_input.tool_name,
                error=str(exc),
            )
            return self._failure(
                tool_input.tool_name,
                f"Drive tool execution failed: {type(exc).__name__}",
                started=started,
            )
        finally:
            self.service.drive.finish_operation()

    async def _live_precondition(self, tool_input: ToolInput) -> tuple[str | None, str | None]:
        """Fetch version for stale-check and etag for Google If-Match (M3)."""
        if tool_input.tool_name not in STALE_CHECK_VERSION_TOOLS:
            return None, None
        if expected_target_fingerprint_from_arguments(tool_input.arguments) is None:
            return None, None
        file_id = str(tool_input.arguments.get("file_id") or "")
        if not file_id:
            return None, None
        try:
            meta = await self.service.get_metadata(
                file_id,
                supports_all_drives=bool(tool_input.arguments.get("supports_all_drives", True)),
            )
            return meta.version, meta.etag
        except (AppError, OSError, TimeoutError, TypeError, ValueError):
            return None, None

    async def invoke(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Alias used by generic tool runtimes."""
        return await self.execute(tool_input, context)

    async def _dispatch(
        self, name: str, args: dict[str, Any], *, if_match: str | None = None
    ) -> Any:
        supports_all_drives = bool(args.get("supports_all_drives", True))

        if name == "drive.search_files":
            return await self.service.search_files(
                query=str(args.get("query") or "") if args.get("query") is not None else None,
                folder_id=args.get("folder_id"),
                page_size=int(args.get("page_size", 20)),
                page_token=args.get("page_token"),
                order_by=str(args.get("order_by") or "modifiedTime desc"),
                spaces=str(args.get("spaces") or "drive"),
                include_trashed=bool(args.get("include_trashed", False)),
                supports_all_drives=supports_all_drives,
                include_items_from_all_drives=bool(args.get("include_items_from_all_drives", True)),
                corpora=args.get("corpora"),
                drive_id=args.get("drive_id"),
            )
        if name == "drive.list_folder":
            return await self.service.list_folder(
                folder_id=str(args.get("folder_id") or "root"),
                page_size=int(args.get("page_size", 50)),
                page_token=args.get("page_token"),
                order_by=str(args.get("order_by") or "folder,name"),
                include_trashed=bool(args.get("include_trashed", False)),
                supports_all_drives=supports_all_drives,
            )
        if name == "drive.get_metadata":
            file_id = str(args.get("file_id") or "")
            if not file_id:
                raise ValidationError("file_id is required.")
            return await self.service.get_metadata(
                file_id,
                supports_all_drives=supports_all_drives,
            )
        if name == "drive.download_file":
            file_id = str(args.get("file_id") or "")
            if not file_id:
                raise ValidationError("file_id is required.")
            return await self.service.download_file(
                file_id,
                export_mime_type=args.get("export_mime_type"),
                acknowledge_abuse=bool(args.get("acknowledge_abuse", True)),
                supports_all_drives=supports_all_drives,
            )
        if name == "drive.upload_file":
            name_arg = str(args.get("name") or "")
            content_arg = args.get("content", "")
            if not name_arg or content_arg is None:
                raise ValidationError("name and content are required.")
            parents = [args["folder_id"]] if args.get("folder_id") else []
            request = DriveUploadRequest(
                name=name_arg,
                content=content_arg,
                mime_type=str(args.get("mime_type") or "application/octet-stream"),
                parents=parents,
                description=args.get("description"),
                starred=bool(args.get("starred", False)),
            )
            return await self.service.upload_file(
                request,
                supports_all_drives=supports_all_drives,
            )
        if name == "drive.create_folder":
            name_arg = str(args.get("name") or "")
            if not name_arg:
                raise ValidationError("name is required.")
            return await self.service.create_folder(
                name=name_arg,
                parent_folder_id=args.get("parent_folder_id"),
                description=args.get("description"),
                supports_all_drives=supports_all_drives,
            )
        if name == "drive.move_file":
            file_id = str(args.get("file_id") or "")
            dest = str(args.get("destination_folder_id") or "")
            if not file_id or not dest:
                raise ValidationError("file_id and destination_folder_id are required.")
            return await self.service.move_file(
                file_id=file_id,
                destination_folder_id=dest,
                source_folder_id=args.get("source_folder_id"),
                supports_all_drives=supports_all_drives,
                if_match=if_match,
            )
        if name == "drive.rename_file":
            file_id = str(args.get("file_id") or "")
            new_name = str(args.get("new_name") or "")
            if not file_id or not new_name:
                raise ValidationError("file_id and new_name are required.")
            return await self.service.rename_file(
                file_id=file_id,
                new_name=new_name,
                supports_all_drives=supports_all_drives,
                if_match=if_match,
            )
        if name == "drive.delete_file":
            file_id = str(args.get("file_id") or "")
            if not file_id:
                raise ValidationError("file_id is required.")
            return await self.service.delete_file(
                file_id=file_id,
                permanent=bool(args.get("permanent", False)),
                supports_all_drives=supports_all_drives,
                if_match=if_match,
            )
        if name == "drive.update_permissions":
            file_id = str(args.get("file_id") or "")
            if not file_id:
                raise ValidationError("file_id is required.")
            return await self.service.update_permissions(
                file_id=file_id,
                role=str(args.get("role") or "reader"),
                type=str(args.get("type") or "user"),
                email_address=args.get("email_address"),
                domain=args.get("domain"),
                permission_id=args.get("permission_id"),
                remove=bool(args.get("remove", False)),
                send_notification_email=bool(args.get("send_notification_email", True)),
                email_message=args.get("email_message"),
                transfer_ownership=bool(args.get("transfer_ownership", False)),
                supports_all_drives=supports_all_drives,
                if_match=if_match,
            )
        raise ValidationError("Drive tool is not registered.")

    def _metadata(self, tool_name: str, started: float) -> ToolExecutionMetadata:
        return ToolExecutionMetadata(
            tool_name=tool_name,
            latency_ms=(time.perf_counter() - started) * 1000,
            retry_count=self.service.drive.last_operation_retry_count,
        )

    def _failure(
        self,
        tool_name: str,
        message: str,
        *,
        started: float | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            error=message,
            metadata=self._metadata(tool_name, started or time.perf_counter()),
        )


__all__ = [
    "DRIVE_TOOL_DEFINITIONS",
    "GoogleDriveTools",
    "build_drive_tool_registry",
    "drive_tool_definitions",
]
