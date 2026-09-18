"""Deterministic Google Drive v3 adapter.

Treats Google Drive as a file system interface, completely separated from RAG.
Provider payloads are normalized at this boundary; callers never need to reason
about Google Drive wire formats, export conversions, or multipart protocols.
"""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from app.domain.errors import ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models.google.drive import (
    GOOGLE_FOLDER_MIMETYPE,
    GOOGLE_NON_EXPORTABLE_MIME_TYPES,
    SUPPORTED_EXPORT_MIME_TYPES,
    DriveDownloadResult,
    DriveFile,
    DriveFilePage,
    DriveMutationResult,
    DrivePermissionResult,
    DriveUploadRequest,
    canonicalize_role,
    get_default_export_mime_type,
)
from app.integrations.google_common import (
    GoogleResourceAdapter,
    RetryPolicy,
    if_match_headers,
    require_list,
    require_object,
)

from .parsing import (
    DRIVE_FILE_FIELDS,
    DRIVE_FILE_SCOPE,
    DRIVE_MAX_PAGE_SIZE,
    DRIVE_PAGE_FIELDS,
    DRIVE_PERMISSION_FIELDS,
    DRIVE_READONLY_SCOPE,
    DRIVE_SCOPE,
    TEXT_DECODE_MAX_BYTES,
    _file_from_payload,
    _permission_from_payload,
    _validate_identifier,
    _validate_page_size,
)

if TYPE_CHECKING:
    from app.services.google.auth import GoogleApiClient


class DriveAdapter(GoogleResourceAdapter):
    """Google Drive v3 API adapter implementing domain operations."""

    def __init__(
        self,
        client: GoogleApiClient,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep: Any = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if retry_policy is not None:
            kwargs["retry_policy"] = retry_policy
        if sleep is not None:
            kwargs["sleep"] = sleep
        super().__init__(client, **kwargs)

    async def search_files(
        self,
        query: str | None = None,
        *,
        folder_id: str | None = None,
        page_size: int = 20,
        page_token: str | None = None,
        order_by: str = "modifiedTime desc",
        spaces: str = "drive",
        include_trashed: bool = False,
        supports_all_drives: bool = True,
        include_items_from_all_drives: bool = True,
        corpora: str | None = None,
        drive_id: str | None = None,
    ) -> DriveFilePage:
        """Search and list files matching Drive v3 query expressions."""
        limit = _validate_page_size(page_size)
        query_parts: list[str] = []

        if not include_trashed:
            query_parts.append("trashed = false")
        if folder_id and folder_id.strip():
            normalized_folder_id = _validate_identifier(folder_id, "folder_id")
            query_parts.append(f"'{normalized_folder_id}' in parents")
        if query and query.strip():
            query_parts.append(f"({query.strip()})")

        q_string = " and ".join(query_parts) if query_parts else None

        params: dict[str, Any] = {
            "pageSize": limit,
            "fields": DRIVE_PAGE_FIELDS,
            "supportsAllDrives": supports_all_drives,
            "includeItemsFromAllDrives": include_items_from_all_drives,
            "spaces": spaces.strip() if spaces else "drive",
        }
        if q_string:
            params["q"] = q_string
        if page_token and page_token.strip():
            params["pageToken"] = page_token.strip()
        if order_by and order_by.strip():
            params["orderBy"] = order_by.strip()
        if corpora and corpora.strip():
            params["corpora"] = corpora.strip()
        if drive_id and drive_id.strip():
            params["driveId"] = _validate_identifier(drive_id, "drive_id")

        payload = await self._request_json(
            "GET",
            "/drive/v3/files",
            params=params,
            operation="Google Drive file search",
        )
        data = require_object(payload, "Google Drive file search")
        file_items = require_list(payload, "files", "Google Drive file search")
        files = [_file_from_payload(item) for item in file_items]

        next_token = data.get("nextPageToken")
        incomplete = bool(data.get("incompleteSearch", False))

        return DriveFilePage(
            items=files,
            next_page_token=str(next_token).strip() if next_token else None,
            incomplete_search=incomplete,
        )

    async def list_folder(
        self,
        folder_id: str = "root",
        *,
        page_size: int = 50,
        page_token: str | None = None,
        order_by: str = "folder,name",
        include_trashed: bool = False,
        supports_all_drives: bool = True,
    ) -> DriveFilePage:
        """List files and subfolders contained within a given folder."""
        normalized_folder_id = _validate_identifier(folder_id, "folder_id")
        return await self.search_files(
            folder_id=normalized_folder_id,
            page_size=page_size,
            page_token=page_token,
            order_by=order_by,
            include_trashed=include_trashed,
            supports_all_drives=supports_all_drives,
        )

    async def get_metadata(
        self,
        file_id: str,
        *,
        supports_all_drives: bool = True,
    ) -> DriveFile:
        """Retrieve complete metadata for a file or folder by ID."""
        normalized_file_id = _validate_identifier(file_id, "file_id")
        params = {
            "fields": DRIVE_FILE_FIELDS,
            "supportsAllDrives": supports_all_drives,
        }
        payload = await self._request_json(
            "GET",
            f"/drive/v3/files/{quote(normalized_file_id)}",
            params=params,
            operation="Google Drive file metadata",
        )
        return _file_from_payload(payload)

    async def download_file(
        self,
        file_id: str,
        *,
        export_mime_type: str | None = None,
        acknowledge_abuse: bool = True,
        supports_all_drives: bool = True,
    ) -> DriveDownloadResult:
        """Download binary content or export Google-native documents."""
        normalized_file_id = _validate_identifier(file_id, "file_id")
        metadata = await self.get_metadata(
            normalized_file_id, supports_all_drives=supports_all_drives
        )

        if metadata.is_folder:
            raise DomainValidationError(
                f"Cannot download folder '{normalized_file_id}'. Folders cannot be downloaded directly.",
                details={"file_id": normalized_file_id, "mime_type": metadata.mime_type},
            )

        if metadata.is_shortcut:
            raise DomainValidationError(
                f"Cannot download shortcut '{normalized_file_id}' directly. Please download the target file.",
                details={"file_id": normalized_file_id, "mime_type": metadata.mime_type},
            )

        if metadata.mime_type in GOOGLE_NON_EXPORTABLE_MIME_TYPES:
            raise DomainValidationError(
                f"Cannot download or export Google Workspace resource '{metadata.name}' of type '{metadata.mime_type}'. "
                "This resource type does not support file export.",
                details={"file_id": normalized_file_id, "mime_type": metadata.mime_type},
            )

        if metadata.is_google_native:
            supported = SUPPORTED_EXPORT_MIME_TYPES.get(metadata.mime_type, frozenset())
            if export_mime_type and export_mime_type.strip():
                target_mime = export_mime_type.strip()
                if supported and target_mime not in supported:
                    raise DomainValidationError(
                        f"Export MIME type '{target_mime}' is not supported for Google document type '{metadata.mime_type}'.",
                        details={
                            "requested_export_mime_type": target_mime,
                            "original_mime_type": metadata.mime_type,
                            "supported_export_mime_types": sorted(supported),
                        },
                    )
            else:
                target_mime = get_default_export_mime_type(metadata.mime_type)
                if not target_mime:
                    raise DomainValidationError(
                        f"No default export format configured for '{metadata.mime_type}'.",
                        details={"supported_export_mime_types": sorted(supported)},
                    )

            raw_bytes, _ = await self._request_bytes(
                "GET",
                f"/drive/v3/files/{quote(normalized_file_id)}/export",
                params={"mimeType": target_mime},
                operation="Google Drive file export",
            )
            text_content: str | None = None
            if len(raw_bytes) <= TEXT_DECODE_MAX_BYTES and (
                target_mime.startswith("text/")
                or target_mime
                in {
                    "application/json",
                    "application/vnd.google-apps.script+json",
                    "text/csv",
                    "text/tab-separated-values",
                    "text/plain",
                    "text/html",
                }
            ):
                try:
                    text_content = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    text_content = None

            return DriveDownloadResult(
                file_id=metadata.id,
                name=metadata.name,
                mime_type=target_mime,
                original_mime_type=metadata.mime_type,
                size_bytes=len(raw_bytes),
                is_exported=True,
                content_bytes=raw_bytes,
                text_content=text_content,
                md5_checksum=metadata.md5_checksum,
                version=metadata.version,
            )

        # Standard binary or media file download
        raw_bytes, _ = await self._request_bytes(
            "GET",
            f"/drive/v3/files/{quote(normalized_file_id)}",
            params={
                "alt": "media",
                "acknowledgeAbuse": str(acknowledge_abuse).lower(),
                "supportsAllDrives": str(supports_all_drives).lower(),
            },
            operation="Google Drive file download",
        )
        text_content = None
        if len(raw_bytes) <= TEXT_DECODE_MAX_BYTES and (
            metadata.mime_type.startswith("text/")
            or metadata.mime_type
            in {
                "application/json",
                "application/xml",
                "application/javascript",
                "text/plain",
                "text/csv",
                "text/html",
                "text/markdown",
            }
        ):
            try:
                text_content = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text_content = None

        return DriveDownloadResult(
            file_id=metadata.id,
            name=metadata.name,
            mime_type=metadata.mime_type,
            original_mime_type=metadata.mime_type,
            size_bytes=len(raw_bytes),
            is_exported=False,
            content_bytes=raw_bytes,
            text_content=text_content,
            md5_checksum=metadata.md5_checksum,
            version=metadata.version,
        )

    async def upload_file(
        self,
        request: DriveUploadRequest,
        *,
        supports_all_drives: bool = True,
    ) -> DriveFile:
        """Upload a file using atomic multipart metadata + media payload."""
        if not request.name.strip():
            raise DomainValidationError("Uploaded file name cannot be blank.")

        metadata: dict[str, Any] = {
            "name": request.name.strip(),
            "mimeType": request.mime_type.strip() or "application/octet-stream",
        }
        if request.parents:
            metadata["parents"] = [_validate_identifier(p, "parent") for p in request.parents]
        if request.description is not None:
            metadata["description"] = request.description
        if request.starred:
            metadata["starred"] = True
        if request.app_properties:
            metadata["appProperties"] = request.app_properties

        boundary = f"===============antigravity_drive_{secrets.token_hex(16)}=="
        body = (
            b"--"
            + boundary.encode("utf-8")
            + b"\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            + json.dumps(metadata).encode("utf-8")
            + b"\r\n--"
            + boundary.encode("utf-8")
            + b"\r\nContent-Type: "
            + metadata["mimeType"].encode("utf-8")
            + b"\r\n\r\n"
            + request.raw_bytes
            + b"\r\n--"
            + boundary.encode("utf-8")
            + b"--\r\n"
        )
        content_type = f"multipart/related; boundary={boundary}"
        url = (
            f"https://www.googleapis.com/upload/drive/v3/files?"
            f"uploadType=multipart&supportsAllDrives={str(supports_all_drives).lower()}"
            f"&fields={quote(DRIVE_FILE_FIELDS)}"
        )
        response = await self._request_raw(
            "POST",
            url,
            content=body,
            headers={"Content-Type": content_type},
            operation="Google Drive file upload",
            retryable=False,
        )
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise ExternalServiceError(
                "Google returned invalid JSON for file upload.", service_name="drive"
            ) from exc
        return _file_from_payload(payload)

    async def create_folder(
        self,
        name: str,
        *,
        parent_folder_id: str | None = None,
        description: str | None = None,
        supports_all_drives: bool = True,
    ) -> DriveFile:
        """Create a new folder."""
        normalized_name = name.strip()
        if not normalized_name:
            raise DomainValidationError("Folder name cannot be blank.")

        metadata: dict[str, Any] = {
            "name": normalized_name,
            "mimeType": GOOGLE_FOLDER_MIMETYPE,
        }
        if parent_folder_id and parent_folder_id.strip():
            metadata["parents"] = [_validate_identifier(parent_folder_id, "parent_folder_id")]
        if description and description.strip():
            metadata["description"] = description.strip()

        params = {
            "supportsAllDrives": supports_all_drives,
            "fields": DRIVE_FILE_FIELDS,
        }
        payload = await self._request_json(
            "POST",
            "/drive/v3/files",
            params=params,
            json=metadata,
            operation="Google Drive folder creation",
            retryable=False,
        )
        return _file_from_payload(payload)

    async def move_file(
        self,
        file_id: str,
        destination_folder_id: str,
        *,
        source_folder_id: str | None = None,
        supports_all_drives: bool = True,
        if_match: str | None = None,
    ) -> DriveFile:
        """Move a file to a new parent folder."""
        normalized_file_id = _validate_identifier(file_id, "file_id")
        normalized_dest = _validate_identifier(destination_folder_id, "destination_folder_id")

        removals: str | None = None
        if source_folder_id is not None:
            removals = _validate_identifier(source_folder_id, "source_folder_id")
        else:
            current = await self.get_metadata(
                normalized_file_id, supports_all_drives=supports_all_drives
            )
            if current.parents:
                removals = ",".join(_validate_identifier(p, "parent") for p in current.parents)

        params: dict[str, Any] = {
            "addParents": normalized_dest,
            "supportsAllDrives": supports_all_drives,
            "fields": DRIVE_FILE_FIELDS,
        }
        if removals:
            params["removeParents"] = removals

        payload = await self._request_json(
            "PATCH",
            f"/drive/v3/files/{quote(normalized_file_id)}",
            params=params,
            headers=if_match_headers(if_match),
            operation="Google Drive file move",
            retryable=False,
        )
        return _file_from_payload(payload)

    async def rename_file(
        self,
        file_id: str,
        new_name: str,
        *,
        supports_all_drives: bool = True,
        if_match: str | None = None,
    ) -> DriveFile:
        """Rename a file or folder."""
        normalized_file_id = _validate_identifier(file_id, "file_id")
        normalized_name = new_name.strip()
        if not normalized_name:
            raise DomainValidationError("New file name cannot be blank.")

        params = {
            "supportsAllDrives": supports_all_drives,
            "fields": DRIVE_FILE_FIELDS,
        }
        payload = await self._request_json(
            "PATCH",
            f"/drive/v3/files/{quote(normalized_file_id)}",
            params=params,
            json={"name": normalized_name},
            headers=if_match_headers(if_match),
            operation="Google Drive file rename",
            retryable=False,
        )
        return _file_from_payload(payload)

    async def delete_file(
        self,
        file_id: str,
        *,
        permanent: bool = False,
        supports_all_drives: bool = True,
        if_match: str | None = None,
    ) -> DriveMutationResult:
        """Trash or permanently delete a file or folder."""
        normalized_file_id = _validate_identifier(file_id, "file_id")
        params = {"supportsAllDrives": supports_all_drives}
        precondition = if_match_headers(if_match)

        if permanent:
            await self._request_json(
                "DELETE",
                f"/drive/v3/files/{quote(normalized_file_id)}",
                params=params,
                headers=precondition,
                allow_empty=True,
                operation="Google Drive file permanent deletion",
                retryable=False,
            )
            return DriveMutationResult(
                resource_id=normalized_file_id,
                operation="delete",
                deleted=True,
                trashed=False,
            )

        # Move to trash (reversible)
        await self._request_json(
            "PATCH",
            f"/drive/v3/files/{quote(normalized_file_id)}",
            params=params,
            json={"trashed": True},
            headers=precondition,
            operation="Google Drive file trash",
            retryable=False,
        )
        return DriveMutationResult(
            resource_id=normalized_file_id,
            operation="trash",
            deleted=False,
            trashed=True,
        )

    async def update_permissions(
        self,
        file_id: str,
        *,
        role: str = "reader",
        type: str = "user",
        email_address: str | None = None,
        domain: str | None = None,
        permission_id: str | None = None,
        remove: bool = False,
        send_notification_email: bool = True,
        email_message: str | None = None,
        transfer_ownership: bool = False,
        supports_all_drives: bool = True,
        if_match: str | None = None,
    ) -> DrivePermissionResult:
        """Create, update, or remove a sharing permission on a file or folder."""
        normalized_file_id = _validate_identifier(file_id, "file_id")
        canonical_role = canonicalize_role(role)
        normalized_type = type.strip().lower()

        if remove:
            if not permission_id or not permission_id.strip():
                raise DomainValidationError("permission_id is required to remove a permission.")
            normalized_perm_id = _validate_identifier(permission_id, "permission_id")
            await self._request_json(
                "DELETE",
                f"/drive/v3/files/{quote(normalized_file_id)}/permissions/{quote(normalized_perm_id)}",
                params={"supportsAllDrives": supports_all_drives},
                headers=if_match_headers(if_match),
                allow_empty=True,
                operation="Google Drive permission removal",
                retryable=False,
            )
            return DrivePermissionResult(
                file_id=normalized_file_id,
                permission=None,
                operation="delete",
                deleted=True,
            )

        if permission_id and permission_id.strip():
            normalized_perm_id = _validate_identifier(permission_id, "permission_id")
            body = {"role": canonical_role}
            params: dict[str, Any] = {
                "supportsAllDrives": supports_all_drives,
                "transferOwnership": transfer_ownership,
                "fields": DRIVE_PERMISSION_FIELDS,
            }
            payload = await self._request_json(
                "PATCH",
                f"/drive/v3/files/{quote(normalized_file_id)}/permissions/{quote(normalized_perm_id)}",
                params=params,
                json=body,
                headers=if_match_headers(if_match),
                operation="Google Drive permission update",
                retryable=False,
            )
            return DrivePermissionResult(
                file_id=normalized_file_id,
                permission=_permission_from_payload(payload),
                operation="update",
                deleted=False,
            )

        # Create new permission with type coupling validation
        if normalized_type in ("user", "group") and not (email_address and email_address.strip()):
            raise DomainValidationError(
                f"email_address is required when creating a permission of type '{normalized_type}'."
            )
        if normalized_type == "domain" and not (domain and domain.strip()):
            raise DomainValidationError(
                "domain is required when creating a permission of type 'domain'."
            )

        body = {
            "role": canonical_role,
            "type": normalized_type,
        }
        if email_address and email_address.strip():
            body["emailAddress"] = email_address.strip()
        if domain and domain.strip():
            body["domain"] = domain.strip()

        params = {
            "supportsAllDrives": supports_all_drives,
            "sendNotificationEmail": send_notification_email,
            "transferOwnership": transfer_ownership,
            "fields": DRIVE_PERMISSION_FIELDS,
        }
        if email_message and email_message.strip():
            params["emailMessage"] = email_message.strip()

        payload = await self._request_json(
            "POST",
            f"/drive/v3/files/{quote(normalized_file_id)}/permissions",
            params=params,
            json=body,
            headers=if_match_headers(if_match),
            operation="Google Drive permission creation",
            retryable=False,
        )
        return DrivePermissionResult(
            file_id=normalized_file_id,
            permission=_permission_from_payload(payload),
            operation="create",
            deleted=False,
        )


GoogleDriveAdapter = DriveAdapter

__all__ = [
    "DRIVE_FILE_FIELDS",
    "DRIVE_FILE_SCOPE",
    "DRIVE_MAX_PAGE_SIZE",
    "DRIVE_PAGE_FIELDS",
    "DRIVE_PERMISSION_FIELDS",
    "DRIVE_READONLY_SCOPE",
    "DRIVE_SCOPE",
    "DriveAdapter",
    "GoogleDriveAdapter",
    "TEXT_DECODE_MAX_BYTES",
]
