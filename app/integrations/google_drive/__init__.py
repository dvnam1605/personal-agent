"""Google Drive adapter and file operation utilities."""

from .adapter import DriveAdapter, GoogleDriveAdapter
from .parsing import (
    DRIVE_FILE_FIELDS,
    DRIVE_FILE_SCOPE,
    DRIVE_MAX_PAGE_SIZE,
    DRIVE_PAGE_FIELDS,
    DRIVE_PERMISSION_FIELDS,
    DRIVE_READONLY_SCOPE,
    DRIVE_SCOPE,
    TEXT_DECODE_MAX_BYTES,
    _validate_identifier,
    _validate_page_size,
)

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
    "_validate_identifier",
    "_validate_page_size",
]
