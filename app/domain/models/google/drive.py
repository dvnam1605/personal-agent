"""Provider-neutral Google Drive domain contracts and models.

Google Drive is treated as a file system interface separate from RAG retrieval.
These models represent the stable boundary for Drive files, folders, downloads,
uploads, mutations, and permissions.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Google Workspace Native MIME Types
GOOGLE_DOC_MIMETYPE = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIMETYPE = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES_MIMETYPE = "application/vnd.google-apps.presentation"
GOOGLE_DRAWING_MIMETYPE = "application/vnd.google-apps.drawing"
GOOGLE_FOLDER_MIMETYPE = "application/vnd.google-apps.folder"
GOOGLE_SHORTCUT_MIMETYPE = "application/vnd.google-apps.shortcut"
GOOGLE_FORM_MIMETYPE = "application/vnd.google-apps.form"
GOOGLE_SITE_MIMETYPE = "application/vnd.google-apps.site"
GOOGLE_SCRIPT_MIMETYPE = "application/vnd.google-apps.script"
GOOGLE_MAP_MIMETYPE = "application/vnd.google-apps.map"

# Google Workspace native types that support the /export endpoint
GOOGLE_EXPORTABLE_MIME_TYPES: frozenset[str] = frozenset(
    {
        GOOGLE_DOC_MIMETYPE,
        GOOGLE_SHEET_MIMETYPE,
        GOOGLE_SLIDES_MIMETYPE,
        GOOGLE_DRAWING_MIMETYPE,
        GOOGLE_SCRIPT_MIMETYPE,
    }
)

# Google Workspace native types that cannot be exported via /export (V1 non-exportable)
GOOGLE_NON_EXPORTABLE_MIME_TYPES: frozenset[str] = frozenset(
    {
        GOOGLE_FOLDER_MIMETYPE,
        GOOGLE_SHORTCUT_MIMETYPE,
        GOOGLE_FORM_MIMETYPE,
        GOOGLE_SITE_MIMETYPE,
        GOOGLE_MAP_MIMETYPE,
    }
)

GOOGLE_NATIVE_MIME_TYPES: frozenset[str] = (
    GOOGLE_EXPORTABLE_MIME_TYPES | GOOGLE_NON_EXPORTABLE_MIME_TYPES
)

# Standard default export mappings for exportable Google Workspace documents
DEFAULT_EXPORT_MIME_TYPES: dict[str, str] = {
    GOOGLE_DOC_MIMETYPE: "text/plain",
    GOOGLE_SHEET_MIMETYPE: "text/csv",
    GOOGLE_SLIDES_MIMETYPE: "application/pdf",
    GOOGLE_DRAWING_MIMETYPE: "image/png",
    GOOGLE_SCRIPT_MIMETYPE: "application/vnd.google-apps.script+json",
}

# Known supported export targets per Google native MIME type
SUPPORTED_EXPORT_MIME_TYPES: dict[str, frozenset[str]] = {
    GOOGLE_DOC_MIMETYPE: frozenset(
        {
            "text/plain",
            "text/html",
            "application/pdf",
            "application/rtf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.oasis.opendocument.text",
            "application/epub+zip",
        }
    ),
    GOOGLE_SHEET_MIMETYPE: frozenset(
        {
            "text/csv",
            "text/tab-separated-values",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/x-vnd.oasis.opendocument.spreadsheet",
            "application/zip",
        }
    ),
    GOOGLE_SLIDES_MIMETYPE: frozenset(
        {
            "text/plain",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.oasis.opendocument.presentation",
        }
    ),
    GOOGLE_DRAWING_MIMETYPE: frozenset(
        {
            "image/png",
            "image/jpeg",
            "image/svg+xml",
            "application/pdf",
        }
    ),
    GOOGLE_SCRIPT_MIMETYPE: frozenset(
        {
            "application/vnd.google-apps.script+json",
        }
    ),
}

CANONICAL_ROLES: dict[str, str] = {
    "owner": "owner",
    "organizer": "organizer",
    "fileorganizer": "fileOrganizer",
    "writer": "writer",
    "commenter": "commenter",
    "reader": "reader",
}


def canonicalize_role(value: str) -> str:
    """Normalize permission role string to Google Drive API canonical casing."""
    normalized = value.strip().lower()
    canonical = CANONICAL_ROLES.get(normalized)
    if not canonical:
        raise ValueError(
            f"Invalid permission role: {value}. Must be one of {sorted(CANONICAL_ROLES.values())}."
        )
    return canonical


def is_google_native_document(mime_type: str) -> bool:
    """Return True if the mime type is any Google Workspace native resource."""
    return mime_type in GOOGLE_NATIVE_MIME_TYPES


def is_exportable_google_native(mime_type: str) -> bool:
    """Return True if the mime type is a Google document that supports file export."""
    return mime_type in GOOGLE_EXPORTABLE_MIME_TYPES


def is_folder(mime_type: str) -> bool:
    """Return True if the mime type represents a Google Drive folder."""
    return mime_type == GOOGLE_FOLDER_MIMETYPE


def is_shortcut(mime_type: str) -> bool:
    """Return True if the mime type represents a Google Drive shortcut."""
    return mime_type == GOOGLE_SHORTCUT_MIMETYPE


def get_default_export_mime_type(mime_type: str) -> str | None:
    """Return the default export MIME type for an exportable Google-native format."""
    return DEFAULT_EXPORT_MIME_TYPES.get(mime_type)


def normalize_datetime(value: datetime | None) -> datetime | None:
    """Ensure datetime is timezone-aware and normalized to UTC."""
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_rfc3339_timestamp(value: object) -> datetime | None:
    """Parse an RFC3339 timestamp string into an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return normalize_datetime(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return datetime.fromisoformat(normalized.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


class DriveFile(BaseModel):
    """Normalized Google Drive file or folder metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    mime_type: str = Field(default="application/octet-stream", min_length=1)
    description: str | None = None
    starred: bool = False
    trashed: bool = False
    parents: list[str] = Field(default_factory=list)
    properties: dict[str, str] = Field(default_factory=dict)
    app_properties: dict[str, str] = Field(default_factory=dict)
    spaces: list[str] = Field(default_factory=list)
    version: str | None = None
    etag: str | None = None
    web_content_link: str | None = None
    web_view_link: str | None = None
    icon_link: str | None = None
    has_thumbnail: bool = False
    thumbnail_link: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None
    viewed_by_me_at: datetime | None = None
    shared_with_me_at: datetime | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    quota_bytes_used: int | None = Field(default=None, ge=0)
    head_revision_id: str | None = None
    md5_checksum: str | None = None
    sha1_checksum: str | None = None
    sha256_checksum: str | None = None
    original_filename: str | None = None
    file_extension: str | None = None
    export_links: dict[str, str] = Field(default_factory=dict)
    shared: bool = False
    owned_by_me: bool = True

    @field_validator("id", "name", "mime_type", mode="after")
    @classmethod
    def normalize_strings(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Required string field cannot be blank.")
        return normalized

    @field_validator(
        "created_at", "modified_at", "viewed_by_me_at", "shared_with_me_at", mode="after"
    )
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        return normalize_datetime(value)

    @property
    def is_folder(self) -> bool:
        """Whether this resource is a folder."""
        return is_folder(self.mime_type)

    @property
    def is_shortcut(self) -> bool:
        """Whether this resource is a Drive shortcut."""
        return is_shortcut(self.mime_type)

    @property
    def is_google_native(self) -> bool:
        """Whether this resource is an exportable Google-native document."""
        return is_exportable_google_native(self.mime_type)


class DriveFilePage(BaseModel):
    """Stable paginated listing of Drive files."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[DriveFile] = Field(default_factory=list)
    next_page_token: str | None = None
    incomplete_search: bool = False

    @field_validator("next_page_token", mode="after")
    @classmethod
    def normalize_page_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DriveDownloadResult(BaseModel):
    """Normalized output from downloading or exporting a Drive file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    mime_type: str = Field(..., min_length=1)
    original_mime_type: str = Field(..., min_length=1)
    size_bytes: int = Field(..., ge=0)
    is_exported: bool = False
    content_bytes: bytes = Field(default=b"", repr=False, exclude=True)
    text_content: str | None = None
    md5_checksum: str | None = None
    version: str | None = None


class DriveUploadRequest(BaseModel):
    """Typed request for uploading a file to Google Drive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(..., min_length=1)
    content: bytes | str
    mime_type: str = "application/octet-stream"
    parents: list[str] = Field(default_factory=list)
    description: str | None = None
    starred: bool = False
    app_properties: dict[str, str] = Field(default_factory=dict)

    @field_validator("name", mode="after")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("File name cannot be blank.")
        return normalized

    @property
    def raw_bytes(self) -> bytes:
        """Return content as bytes regardless of whether string or bytes was supplied."""
        if isinstance(self.content, str):
            return self.content.encode("utf-8")
        return self.content


class DrivePermission(BaseModel):
    """Normalized Google Drive sharing permission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    email_address: str | None = None
    display_name: str | None = None
    domain: str | None = None
    allow_file_discovery: bool = False
    deleted: bool = False

    @field_validator("role", mode="after")
    @classmethod
    def validate_role(cls, value: str) -> str:
        return canonicalize_role(value)

    @field_validator("type", mode="after")
    @classmethod
    def validate_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        valid_types = {"user", "group", "domain", "anyone"}
        if normalized not in valid_types:
            raise ValueError(
                f"Invalid permission type: {value}. Must be one of {sorted(valid_types)}."
            )
        return normalized


class DrivePermissionResult(BaseModel):
    """Normalized result of creating, modifying, or deleting a Drive permission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_id: str = Field(..., min_length=1)
    permission: DrivePermission | None = None
    operation: str = Field(..., min_length=1)
    deleted: bool = False


class DriveMutationResult(BaseModel):
    """Normalized result of a mutating Drive operation (delete, trash, move, rename)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str = Field(..., min_length=1)
    operation: str = Field(..., min_length=1)
    deleted: bool = False
    trashed: bool = False


__all__ = [
    "CANONICAL_ROLES",
    "DEFAULT_EXPORT_MIME_TYPES",
    "DriveDownloadResult",
    "DriveFile",
    "DriveFilePage",
    "DriveMutationResult",
    "DrivePermission",
    "DrivePermissionResult",
    "DriveUploadRequest",
    "GOOGLE_DOC_MIMETYPE",
    "GOOGLE_DRAWING_MIMETYPE",
    "GOOGLE_EXPORTABLE_MIME_TYPES",
    "GOOGLE_FOLDER_MIMETYPE",
    "GOOGLE_FORM_MIMETYPE",
    "GOOGLE_MAP_MIMETYPE",
    "GOOGLE_NATIVE_MIME_TYPES",
    "GOOGLE_NON_EXPORTABLE_MIME_TYPES",
    "GOOGLE_SCRIPT_MIMETYPE",
    "GOOGLE_SHEET_MIMETYPE",
    "GOOGLE_SHORTCUT_MIMETYPE",
    "GOOGLE_SITE_MIMETYPE",
    "GOOGLE_SLIDES_MIMETYPE",
    "SUPPORTED_EXPORT_MIME_TYPES",
    "canonicalize_role",
    "get_default_export_mime_type",
    "is_exportable_google_native",
    "is_folder",
    "is_google_native_document",
    "is_shortcut",
    "normalize_datetime",
    "parse_rfc3339_timestamp",
]
