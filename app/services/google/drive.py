"""Deterministic Google Drive domain service."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from app.domain.models.google.drive import (
    DriveDownloadResult,
    DriveFile,
    DriveFilePage,
    DriveMutationResult,
    DrivePermissionResult,
    DriveUploadRequest,
)
from app.integrations.google_common import RetryPolicy, Sleep
from app.integrations.google_drive import (
    DRIVE_READONLY_SCOPE,
    DriveAdapter,
)
from app.services.google.auth import AsyncHttpTransport, GoogleApiClient, GoogleOAuthService

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
        retry_policy: RetryPolicy | None = None,
        sleep: Sleep | None = None,
    ) -> DriveService:
        kwargs: dict[str, Any] = {}
        if retry_policy is not None:
            kwargs["retry_policy"] = retry_policy
        if sleep is not None:
            kwargs["sleep"] = sleep
        return cls(DriveAdapter(client, **kwargs))

    @classmethod
    async def for_user(
        cls,
        oauth_service: GoogleOAuthService,
        session: AsyncSession,
        user_id: str,
        *,
        required_scopes: Iterable[str] | None = None,
        transport: AsyncHttpTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        sleep: Sleep | None = None,
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
        return await self.drive.search_files(
            query=query,
            folder_id=folder_id,
            page_size=page_size,
            page_token=page_token,
            order_by=order_by,
            spaces=spaces,
            include_trashed=include_trashed,
            supports_all_drives=supports_all_drives,
            include_items_from_all_drives=include_items_from_all_drives,
            corpora=corpora,
            drive_id=drive_id,
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
        return await self.drive.list_folder(
            folder_id=folder_id,
            page_size=page_size,
            page_token=page_token,
            order_by=order_by,
            include_trashed=include_trashed,
            supports_all_drives=supports_all_drives,
        )

    async def get_metadata(self, file_id: str, *, supports_all_drives: bool = True) -> DriveFile:
        return await self.drive.get_metadata(
            file_id=file_id, supports_all_drives=supports_all_drives
        )

    async def download_file(
        self,
        file_id: str,
        *,
        export_mime_type: str | None = None,
        acknowledge_abuse: bool = True,
        supports_all_drives: bool = True,
    ) -> DriveDownloadResult:
        return await self.drive.download_file(
            file_id=file_id,
            export_mime_type=export_mime_type,
            acknowledge_abuse=acknowledge_abuse,
            supports_all_drives=supports_all_drives,
        )

    async def upload_file(
        self,
        request: DriveUploadRequest,
        *,
        supports_all_drives: bool = True,
    ) -> DriveFile:
        return await self.drive.upload_file(
            request=request, supports_all_drives=supports_all_drives
        )

    async def create_folder(
        self,
        name: str,
        *,
        parent_folder_id: str | None = None,
        description: str | None = None,
        supports_all_drives: bool = True,
    ) -> DriveFile:
        return await self.drive.create_folder(
            name=name,
            parent_folder_id=parent_folder_id,
            description=description,
            supports_all_drives=supports_all_drives,
        )

    async def move_file(
        self,
        file_id: str,
        destination_folder_id: str,
        *,
        source_folder_id: str | None = None,
        supports_all_drives: bool = True,
        if_match: str | None = None,
    ) -> DriveFile:
        return await self.drive.move_file(
            file_id=file_id,
            destination_folder_id=destination_folder_id,
            source_folder_id=source_folder_id,
            supports_all_drives=supports_all_drives,
            if_match=if_match,
        )

    async def rename_file(
        self,
        file_id: str,
        new_name: str,
        *,
        supports_all_drives: bool = True,
        if_match: str | None = None,
    ) -> DriveFile:
        return await self.drive.rename_file(
            file_id=file_id,
            new_name=new_name,
            supports_all_drives=supports_all_drives,
            if_match=if_match,
        )

    async def delete_file(
        self,
        file_id: str,
        *,
        permanent: bool = False,
        supports_all_drives: bool = True,
        if_match: str | None = None,
    ) -> DriveMutationResult:
        return await self.drive.delete_file(
            file_id=file_id,
            permanent=permanent,
            supports_all_drives=supports_all_drives,
            if_match=if_match,
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
        return await self.drive.update_permissions(
            file_id=file_id,
            role=role,
            type=type,
            email_address=email_address,
            domain=domain,
            permission_id=permission_id,
            remove=remove,
            send_notification_email=send_notification_email,
            email_message=email_message,
            transfer_ownership=transfer_ownership,
            supports_all_drives=supports_all_drives,
            if_match=if_match,
        )


GoogleDriveService = DriveService

__all__ = [
    "DriveService",
    "GoogleDriveService",
]
