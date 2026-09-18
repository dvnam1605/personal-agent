"""Google Drive v3 payload parsing, serialization, and validation helpers."""

from __future__ import annotations

from app.domain.errors import ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models.google.drive import (
    DriveFile,
    DrivePermission,
    canonicalize_role,
    parse_rfc3339_timestamp,
)
from app.integrations.google_common import require_object

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

DRIVE_MAX_PAGE_SIZE = 1000
TEXT_DECODE_MAX_BYTES = 1024 * 1024  # 1 MB

DRIVE_FILE_FIELDS = (
    "id,name,mimeType,description,starred,trashed,parents,properties,appProperties,"
    "spaces,version,etag,webContentLink,webViewLink,iconLink,hasThumbnail,thumbnailLink,"
    "createdTime,modifiedTime,viewedByMeTime,sharedWithMeTime,size,quotaBytesUsed,"
    "headRevisionId,md5Checksum,sha1Checksum,sha256Checksum,originalFilename,"
    "fileExtension,exportLinks,shared,ownedByMe"
)

DRIVE_PAGE_FIELDS = f"nextPageToken,incompleteSearch,files({DRIVE_FILE_FIELDS})"

DRIVE_PERMISSION_FIELDS = "id,role,type,emailAddress,displayName,domain,allowFileDiscovery"


def _validate_identifier(value: str, label: str) -> str:
    """Reject identifiers that could escape URL paths or Drive ``q`` quoting.

    Single quotes are refused because folder/drive identifiers are interpolated
    into single-quoted Drive query literals (``'<id>' in parents``); a quote
    would allow tool-argument injection into the query expression.
    """
    normalized = value.strip() if isinstance(value, str) else ""
    if (
        not normalized
        or "/" in normalized
        or "\\" in normalized
        or "?" in normalized
        or "#" in normalized
        or "'" in normalized
        or '"' in normalized
        or ".." in normalized
    ):
        raise DomainValidationError(f"Invalid Google Drive {label}.")
    return normalized


def _validate_page_size(value: int) -> int:
    if not 1 <= value <= DRIVE_MAX_PAGE_SIZE:
        raise DomainValidationError(
            f"Drive page size must be between 1 and {DRIVE_MAX_PAGE_SIZE}.",
            details={"page_size": value},
        )
    return value


def _file_from_payload(payload: object) -> DriveFile:
    """Normalize a Google Drive v3 file resource object."""
    data = require_object(payload, "Google Drive file normalization")
    file_id = data.get("id")
    name = data.get("name")
    if not isinstance(file_id, str) or not file_id.strip():
        raise ExternalServiceError(
            "Google returned a Drive file without an identifier.", service_name="drive"
        )
    if not isinstance(name, str) or not name.strip():
        name = file_id

    def _int_value(key: str) -> int | None:
        val = data.get(key)
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def _dict_value(key: str) -> dict[str, str]:
        val = data.get(key)
        if isinstance(val, dict):
            return {str(k): str(v) for k, v in val.items()}
        return {}

    parents_raw = data.get("parents")
    parents: list[str] = []
    if isinstance(parents_raw, list):
        parents = [str(p).strip() for p in parents_raw if str(p).strip()]

    spaces_raw = data.get("spaces")
    spaces: list[str] = []
    if isinstance(spaces_raw, list):
        spaces = [str(s).strip() for s in spaces_raw if str(s).strip()]

    export_links_raw = data.get("exportLinks")
    export_links: dict[str, str] = {}
    if isinstance(export_links_raw, dict):
        export_links = {str(k): str(v) for k, v in export_links_raw.items()}

    return DriveFile(
        id=file_id.strip(),
        name=name.strip(),
        mime_type=str(data.get("mimeType") or "application/octet-stream").strip(),
        description=data.get("description") if isinstance(data.get("description"), str) else None,
        starred=bool(data.get("starred", False)),
        trashed=bool(data.get("trashed", False)),
        parents=parents,
        properties=_dict_value("properties"),
        app_properties=_dict_value("appProperties"),
        spaces=spaces,
        version=str(data["version"]).strip() if data.get("version") is not None else None,
        etag=str(data["etag"]).strip() if isinstance(data.get("etag"), str) else None,
        web_content_link=(
            str(data["webContentLink"]).strip() if data.get("webContentLink") is not None else None
        ),
        web_view_link=(
            str(data["webViewLink"]).strip() if data.get("webViewLink") is not None else None
        ),
        icon_link=str(data["iconLink"]).strip() if data.get("iconLink") is not None else None,
        has_thumbnail=bool(data.get("hasThumbnail", False)),
        thumbnail_link=(
            str(data["thumbnailLink"]).strip() if data.get("thumbnailLink") is not None else None
        ),
        created_at=parse_rfc3339_timestamp(data.get("createdTime")),
        modified_at=parse_rfc3339_timestamp(data.get("modifiedTime")),
        viewed_by_me_at=parse_rfc3339_timestamp(data.get("viewedByMeTime")),
        shared_with_me_at=parse_rfc3339_timestamp(data.get("sharedWithMeTime")),
        size_bytes=_int_value("size"),
        quota_bytes_used=_int_value("quotaBytesUsed"),
        head_revision_id=(
            str(data["headRevisionId"]).strip() if data.get("headRevisionId") is not None else None
        ),
        md5_checksum=(
            str(data["md5Checksum"]).strip() if data.get("md5Checksum") is not None else None
        ),
        sha1_checksum=(
            str(data["sha1Checksum"]).strip() if data.get("sha1Checksum") is not None else None
        ),
        sha256_checksum=(
            str(data["sha256Checksum"]).strip() if data.get("sha256Checksum") is not None else None
        ),
        original_filename=(
            str(data["originalFilename"]).strip()
            if data.get("originalFilename") is not None
            else None
        ),
        file_extension=(
            str(data["fileExtension"]).strip() if data.get("fileExtension") is not None else None
        ),
        export_links=export_links,
        shared=bool(data.get("shared", False)),
        owned_by_me=bool(data.get("ownedByMe", True)),
    )


def _permission_from_payload(payload: object) -> DrivePermission:
    """Normalize a Google Drive v3 permission resource."""
    data = require_object(payload, "Google Drive permission normalization")
    perm_id = data.get("id")
    role = data.get("role")
    perm_type = data.get("type")
    if not isinstance(perm_id, str) or not perm_id.strip():
        raise ExternalServiceError(
            "Google returned a permission without an ID.", service_name="drive"
        )
    if not isinstance(role, str) or not role.strip():
        role = "reader"
    if not isinstance(perm_type, str) or not perm_type.strip():
        perm_type = "user"

    return DrivePermission(
        id=perm_id.strip(),
        role=canonicalize_role(role),
        type=perm_type.strip().lower(),
        email_address=(
            str(data["emailAddress"]).strip() if data.get("emailAddress") is not None else None
        ),
        display_name=(
            str(data["displayName"]).strip() if data.get("displayName") is not None else None
        ),
        domain=str(data["domain"]).strip() if data.get("domain") is not None else None,
        allow_file_discovery=bool(data.get("allowFileDiscovery", False)),
        deleted=bool(data.get("deleted", False)),
    )


__all__ = [
    "DRIVE_FILE_FIELDS",
    "DRIVE_FILE_SCOPE",
    "DRIVE_MAX_PAGE_SIZE",
    "DRIVE_PAGE_FIELDS",
    "DRIVE_PERMISSION_FIELDS",
    "DRIVE_READONLY_SCOPE",
    "DRIVE_SCOPE",
    "TEXT_DECODE_MAX_BYTES",
    "_file_from_payload",
    "_permission_from_payload",
    "_validate_identifier",
    "_validate_page_size",
]
