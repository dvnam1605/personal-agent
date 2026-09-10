"""Deterministic communication-domain service for Gmail and Contacts."""

from typing import TYPE_CHECKING

from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models import (
    Contact,
    ContactPage,
    ContactResolution,
    ContactResolutionStatus,
    GmailDraft,
    GmailMessage,
    GmailMessagePage,
    GmailMutationResult,
    GmailSendResult,
    GmailThread,
    GmailThreadPage,
)
from app.integrations.google_contacts import CONTACTS_READONLY_SCOPE, ContactsAdapter
from app.integrations.google_gmail import GMAIL_MODIFY_SCOPE, GmailAdapter
from app.services.google.auth import AsyncHttpTransport, GoogleApiClient, GoogleOAuthService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class CommunicationService:
    """Domain service that composes typed Gmail and Contacts adapters.

    It contains no LLM calls and no provider response objects.  Authorization
    is performed before construction through :meth:`for_user`; tests and
    lower-level deterministic workflows can use :meth:`from_client` directly.
    """

    def __init__(self, gmail: GmailAdapter, contacts: ContactsAdapter) -> None:
        self.gmail = gmail
        self.contacts = contacts

    @classmethod
    def from_client(
        cls,
        client: GoogleApiClient,
        *,
        retry_policy=None,
        sleep=None,
    ) -> "CommunicationService":
        """Build the domain service around one already-authorized client."""
        adapter_kwargs = {}
        if retry_policy is not None:
            adapter_kwargs["retry_policy"] = retry_policy
        if sleep is not None:
            adapter_kwargs["sleep"] = sleep
        return cls(
            GmailAdapter(client, **adapter_kwargs),
            ContactsAdapter(client, **adapter_kwargs),
        )

    @classmethod
    async def for_user(
        cls,
        oauth_service: GoogleOAuthService,
        session: "AsyncSession",
        user_id: str,
        *,
        transport: AsyncHttpTransport | None = None,
        retry_policy=None,
        sleep=None,
    ) -> "CommunicationService":
        """Refresh credentials if needed, then build a least-scope client."""
        client = await oauth_service.create_client(
            session,
            user_id,
            required_scopes=[GMAIL_MODIFY_SCOPE, CONTACTS_READONLY_SCOPE],
            transport=transport,
        )
        return cls.from_client(client, retry_policy=retry_policy, sleep=sleep)

    async def search_messages(self, *args, **kwargs) -> GmailMessagePage:
        return await self.gmail.search_messages(*args, **kwargs)

    async def get_message(self, *args, **kwargs) -> GmailMessage:
        return await self.gmail.get_message(*args, **kwargs)

    async def get_thread(self, *args, **kwargs) -> GmailThread:
        return await self.gmail.get_thread(*args, **kwargs)

    async def list_threads(self, *args, **kwargs) -> GmailThreadPage:
        return await self.gmail.list_threads(*args, **kwargs)

    async def create_draft(self, *args, **kwargs) -> GmailDraft:
        return await self.gmail.create_draft(*args, **kwargs)

    async def update_draft(self, *args, **kwargs) -> GmailDraft:
        return await self.gmail.update_draft(*args, **kwargs)

    async def delete_draft(self, *args, **kwargs) -> GmailMutationResult:
        return await self.gmail.delete_draft(*args, **kwargs)

    async def send_draft(self, *args, **kwargs) -> GmailSendResult:
        return await self.gmail.send_draft(*args, **kwargs)

    async def reply(self, *args, **kwargs) -> GmailSendResult:
        return await self.gmail.reply(*args, **kwargs)

    async def forward(self, *args, **kwargs) -> GmailSendResult:
        return await self.gmail.forward(*args, **kwargs)

    async def archive(self, *args, **kwargs) -> GmailMutationResult:
        return await self.gmail.archive(*args, **kwargs)

    async def trash(self, *args, **kwargs) -> GmailMutationResult:
        return await self.gmail.trash(*args, **kwargs)

    async def add_label(self, *args, **kwargs) -> GmailMutationResult:
        return await self.gmail.add_label(*args, **kwargs)

    async def remove_label(self, *args, **kwargs) -> GmailMutationResult:
        return await self.gmail.remove_label(*args, **kwargs)

    async def search_contacts(self, *args, **kwargs) -> ContactPage:
        return await self.contacts.search(*args, **kwargs)

    async def get_contact(self, resource_name: str) -> Contact:
        return await self.contacts.get(resource_name)

    async def resolve_person(
        self,
        query: str,
        *,
        page_size: int = 30,
        max_pages: int = 8,
    ) -> ContactResolution:
        """Resolve exact email/name matches and return ambiguity explicitly."""
        original_query = query.strip()
        if not original_query:
            raise DomainValidationError("A non-blank person query is required.")
        if not 1 <= max_pages <= 20:
            raise DomainValidationError("Contact resolution max_pages must be between 1 and 20.")

        query_key = " ".join(original_query.casefold().split())
        candidates: list[Contact] = []
        page_token: str | None = None
        for _ in range(max_pages):
            page = await self.contacts.search(
                original_query,
                page_size=page_size,
                page_token=page_token,
            )
            for contact in page.items:
                if all(existing.resource_name != contact.resource_name for existing in candidates):
                    candidates.append(contact)
            page_token = page.next_page_token
            if not page_token:
                break

        exact = [contact for contact in candidates if self._is_exact_match(contact, query_key)]
        if len(exact) == 1:
            return ContactResolution(
                query=original_query,
                status=ContactResolutionStatus.EXACT,
                contact=exact[0],
                candidates=candidates,
            )
        if len(exact) > 1:
            return ContactResolution(
                query=original_query,
                status=ContactResolutionStatus.AMBIGUOUS,
                candidates=exact,
            )
        if len(candidates) > 1:
            return ContactResolution(
                query=original_query,
                status=ContactResolutionStatus.AMBIGUOUS,
                candidates=candidates,
            )
        return ContactResolution(
            query=original_query,
            status=ContactResolutionStatus.NOT_FOUND,
            candidates=candidates,
        )

    @staticmethod
    def _is_exact_match(contact: Contact, query_key: str) -> bool:
        values = [contact.display_name, contact.full_name, contact.given_name, contact.family_name]
        if any(value and " ".join(value.casefold().split()) == query_key for value in values):
            return True
        return query_key in {value.casefold() for value in contact.email_values}


GoogleCommunicationService = CommunicationService


__all__ = ["CommunicationService", "GoogleCommunicationService"]
