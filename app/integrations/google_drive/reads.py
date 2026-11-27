"""Read-side Google Drive v3 operations: search, list, metadata and download.

Split from ``adapter.py`` by responsibility; ``DriveAdapter`` inherits these and adds
the write operations, so callers keep using ``DriveAdapter`` only.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models.google.drive import (
    GOOGLE_NON_EXPORTABLE_MIME_TYPES,
    SUPPORTED_EXPORT_MIME_TYPES,
    DriveDownloadResult,
    DriveFile,
    DriveFilePage,
    get_default_export_mime_type,
)
from app.integrations.google_common import GoogleResourceAdapter, require_list, require_object

from .parsing import (
    DRIVE_FILE_FIELDS,
    DRIVE_PAGE_FIELDS,
    TEXT_DECODE_MAX_BYTES,
    _file_from_payload,
    _validate_identifier,
    _validate_page_size,
)


class DriveReadMixin(GoogleResourceAdapter):
    """Drive operations that never change anything in the user's Drive."""

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
