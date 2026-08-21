"""Deterministic Google Drive domain service."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from app.domain.models.drive import (
    DriveDownloadResult,
    DriveFile,
    DriveFilePage,
    DriveMutationResult,
    DrivePermissionResult,
)
from app.integrations.google_drive import (
    DRIVE_READONLY_SCOPE,
    DriveAdapter,
)
from app.services.google_auth import AsyncHttpTransport, GoogleApiClient, GoogleOAuthService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class DriveService:
    """Domain service composing deterministic Drive adapter operations."""

    def __init__(self, drive: DriveAdapter) -> None:
        self.drive = drive

    @classmethod
    def from_client(
        cls,
        client: GoogleApiClient,
        *,
        retry_policy=None,
        sleep=None,
    ) -> DriveService:
        adapter_kwargs: dict[str, Any] = {}
        if retry_policy is not None:
            adapter_kwargs["retry_policy"] = retry_policy
        if sleep is not None:
            adapter_kwargs["sleep"] = sleep
        return cls(DriveAdapter(client, **adapter_kwargs))

    @classmethod
    async def for_user(
        cls,
        oauth_service: GoogleOAuthService,
        session: AsyncSession,
        user_id: str,
        *,
        required_scopes: Iterable[str] | None = None,
        transport: AsyncHttpTransport | None = None,
        retry_policy=None,
        sleep=None,
    ) -> DriveService:
        """Refresh credentials if necessary, then build a least-scope client."""
        client = await oauth_service.create_client(
            session,
            user_id,
            required_scopes=(
                list(required_scopes) if required_scopes is not None else [DRIVE_READONLY_SCOPE]
            ),
            transport=transport,
        )
        return cls.from_client(client, retry_policy=retry_policy, sleep=sleep)

    async def search_files(self, *args: Any, **kwargs: Any) -> DriveFilePage:
        return await self.drive.search_files(*args, **kwargs)

    async def list_folder(self, *args: Any, **kwargs: Any) -> DriveFilePage:
        return await self.drive.list_folder(*args, **kwargs)

    async def get_metadata(self, *args: Any, **kwargs: Any) -> DriveFile:
        return await self.drive.get_metadata(*args, **kwargs)

    async def download_file(self, *args: Any, **kwargs: Any) -> DriveDownloadResult:
        return await self.drive.download_file(*args, **kwargs)

    async def upload_file(self, *args: Any, **kwargs: Any) -> DriveFile:
        return await self.drive.upload_file(*args, **kwargs)

    async def create_folder(self, *args: Any, **kwargs: Any) -> DriveFile:
        return await self.drive.create_folder(*args, **kwargs)

    async def move_file(self, *args: Any, **kwargs: Any) -> DriveFile:
        return await self.drive.move_file(*args, **kwargs)

    async def rename_file(self, *args: Any, **kwargs: Any) -> DriveFile:
        return await self.drive.rename_file(*args, **kwargs)

    async def delete_file(self, *args: Any, **kwargs: Any) -> DriveMutationResult:
        return await self.drive.delete_file(*args, **kwargs)

    async def update_permissions(self, *args: Any, **kwargs: Any) -> DrivePermissionResult:
        return await self.drive.update_permissions(*args, **kwargs)


GoogleDriveService = DriveService

__all__ = [
    "DriveService",
    "GoogleDriveService",
]
