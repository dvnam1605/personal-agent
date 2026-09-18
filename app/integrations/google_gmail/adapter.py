"""Deterministic Gmail API adapter.

This module owns Google wire formats, MIME decoding, pagination, and provider
error boundaries.  Callers receive only provider-neutral communication models.
"""

from collections.abc import Iterable
from typing import Any, Literal, cast
from urllib.parse import quote

from app.domain.errors import ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models import (
    EmailAddress,
    GmailComposeRequest,
    GmailDraft,
    GmailMessage,
    GmailMessagePage,
    GmailMutationResult,
    GmailSendResult,
    GmailThread,
    GmailThreadPage,
)
from app.integrations.google_common import (
    GoogleResourceAdapter,
    require_list,
    require_object,
)

from .parsing import (
    _compose_raw_message,
    _message_from_payload,
    _parse_addresses,
    _recipient_values,
    _summary_from_payload,
    _thread_summary_from_payload,
    _validate_identifier,
    _validate_page_size,
    html_to_text,
)

GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
# gmail.modify is intentionally reused as the read scope: it is the least-privilege
# scope that still permits label/archive mutations, and gmail.readonly would break
# every classified write tool. The alias documents that read paths need nothing wider.
GMAIL_READ_SCOPE = GMAIL_MODIFY_SCOPE
GMAIL_FORMATS = ("minimal", "full", "raw", "metadata")
GmailFormat = Literal["minimal", "full", "raw", "metadata"]


class GmailAdapter(GoogleResourceAdapter):
    """Typed Gmail adapter using the already-authorized common Google client."""

    required_scope = GMAIL_MODIFY_SCOPE

    async def search_messages(
        self,
        query: str = "",
        *,
        page_size: int = 100,
        page_token: str | None = None,
        include_spam_trash: bool = False,
    ) -> GmailMessagePage:
        """Search Gmail messages with provider pagination preserved."""
        params: dict[str, Any] = {
            "maxResults": _validate_page_size(page_size),
            "includeSpamTrash": include_spam_trash,
        }
        if query.strip():
            params["q"] = query.strip()
        if page_token and page_token.strip():
            params["pageToken"] = page_token.strip()
        payload = await self._request_json(
            "GET",
            "/gmail/v1/users/me/messages",
            operation="Gmail message search",
            params=params,
        )
        data = require_object(payload, "Gmail message search")
        values = require_list(data, "messages", "Gmail message search")
        return GmailMessagePage(
            items=[_summary_from_payload(value) for value in values],
            next_page_token=data.get("nextPageToken")
            if isinstance(data.get("nextPageToken"), str)
            else None,
            result_size_estimate=(
                data.get("resultSizeEstimate")
                if isinstance(data.get("resultSizeEstimate"), int)
                else None
            ),
        )

    async def get_message(
        self,
        message_id: str,
        *,
        format: GmailFormat = "full",
    ) -> GmailMessage:
        """Fetch and normalize one Gmail message."""
        if format not in GMAIL_FORMATS:
            raise DomainValidationError("Unsupported Gmail message format.")
        identifier = _validate_identifier(message_id, "message identifier")
        payload = await self._request_json(
            "GET",
            f"/gmail/v1/users/me/messages/{quote(identifier, safe='')}",
            operation="Gmail message lookup",
            params={"format": format},
        )
        return _message_from_payload(payload)

    async def get_thread(
        self,
        thread_id: str,
        *,
        format: GmailFormat = "full",
    ) -> GmailThread:
        """Fetch and normalize one complete Gmail thread."""
        if format not in GMAIL_FORMATS:
            raise DomainValidationError("Unsupported Gmail thread format.")
        identifier = _validate_identifier(thread_id, "thread identifier")
        payload = await self._request_json(
            "GET",
            f"/gmail/v1/users/me/threads/{quote(identifier, safe='')}",
            operation="Gmail thread lookup",
            params={"format": format},
        )
        data = require_object(payload, "Gmail thread lookup")
        messages = data.get("messages", [])
        if not isinstance(messages, list):
            raise ExternalServiceError(
                "Google returned an invalid Gmail thread.", service_name="gmail"
            )
        summary = _thread_summary_from_payload(data)
        return GmailThread(
            **summary.model_dump(),
            messages=[_message_from_payload(value) for value in messages],
        )

    async def list_threads(
        self,
        query: str = "",
        *,
        page_size: int = 100,
        page_token: str | None = None,
        include_spam_trash: bool = False,
    ) -> GmailThreadPage:
        """List normalized thread references with deterministic pagination."""
        params: dict[str, Any] = {
            "maxResults": _validate_page_size(page_size),
            "includeSpamTrash": include_spam_trash,
        }
        if query.strip():
            params["q"] = query.strip()
        if page_token and page_token.strip():
            params["pageToken"] = page_token.strip()
        payload = await self._request_json(
            "GET",
            "/gmail/v1/users/me/threads",
            operation="Gmail thread search",
            params=params,
        )
        data = require_object(payload, "Gmail thread search")
        values = require_list(data, "threads", "Gmail thread search")
        return GmailThreadPage(
            items=[_thread_summary_from_payload(value) for value in values],
            next_page_token=data.get("nextPageToken")
            if isinstance(data.get("nextPageToken"), str)
            else None,
            result_size_estimate=(
                data.get("resultSizeEstimate")
                if isinstance(data.get("resultSizeEstimate"), int)
                else None
            ),
        )

    async def create_draft(
        self,
        to: Iterable[str | EmailAddress] | str | EmailAddress,
        subject: str = "",
        body_text: str = "",
        *,
        cc: Iterable[str | EmailAddress] | str | EmailAddress = (),
        bcc: Iterable[str | EmailAddress] | str | EmailAddress = (),
        body_html: str | None = None,
        thread_id: str | None = None,
    ) -> GmailDraft:
        """Create a draft; sending is a separate explicitly classified action."""
        request = GmailComposeRequest(
            to=_recipient_values(to),
            subject=subject,
            body_text=body_text,
            cc=_recipient_values(cc),
            bcc=_recipient_values(bcc),
            body_html=body_html,
            thread_id=thread_id,
        )
        payload = await self._request_json(
            "POST",
            "/gmail/v1/users/me/drafts",
            operation="Gmail draft creation",
            json={"message": self._compose_message_payload(request)},
        )
        return self._draft_from_payload(payload)

    async def update_draft(
        self,
        draft_id: str,
        to: Iterable[str | EmailAddress] | str | EmailAddress,
        subject: str = "",
        body_text: str = "",
        *,
        cc: Iterable[str | EmailAddress] | str | EmailAddress = (),
        bcc: Iterable[str | EmailAddress] | str | EmailAddress = (),
        body_html: str | None = None,
        thread_id: str | None = None,
    ) -> GmailDraft:
        """Replace a draft deterministically."""
        identifier = _validate_identifier(draft_id, "draft identifier")
        request = GmailComposeRequest(
            to=_recipient_values(to),
            subject=subject,
            body_text=body_text,
            cc=_recipient_values(cc),
            bcc=_recipient_values(bcc),
            body_html=body_html,
            thread_id=thread_id,
        )
        payload = await self._request_json(
            "PUT",
            f"/gmail/v1/users/me/drafts/{quote(identifier, safe='')}",
            operation="Gmail draft update",
            json={"message": self._compose_message_payload(request)},
        )
        return self._draft_from_payload(payload)

    async def delete_draft(self, draft_id: str) -> GmailMutationResult:
        """Delete a draft and return an explicit normalized mutation result."""
        identifier = _validate_identifier(draft_id, "draft identifier")
        await self._request_json(
            "DELETE",
            f"/gmail/v1/users/me/drafts/{quote(identifier, safe='')}",
            operation="Gmail draft deletion",
            allow_empty=True,
        )
        return GmailMutationResult(resource_id=identifier, operation="delete_draft")

    async def send_draft(self, draft_id: str) -> GmailSendResult:
        """Send an existing draft."""
        identifier = _validate_identifier(draft_id, "draft identifier")
        payload = await self._request_json(
            "POST",
            "/gmail/v1/users/me/drafts/send",
            operation="Gmail draft send",
            json={"id": identifier},
        )
        return self._send_result_from_payload(payload)

    async def reply(
        self,
        message_id: str,
        body_text: str,
        *,
        cc: Iterable[str | EmailAddress] | str | EmailAddress = (),
        bcc: Iterable[str | EmailAddress] | str | EmailAddress = (),
        body_html: str | None = None,
    ) -> GmailSendResult:
        """Reply in the original thread using deterministic header resolution."""
        original = await self.get_message(message_id, format="full")
        recipients = _parse_addresses(original.headers.get("reply-to"))
        if not recipients and original.sender is not None:
            recipients = [original.sender]
        if not recipients:
            raise DomainValidationError("The original Gmail message has no reply recipient.")
        subject = original.subject or ""
        if not subject.casefold().startswith("re:"):
            subject = f"Re: {subject}" if subject else "Re:"
        message_payload = self._compose_message_payload(
            GmailComposeRequest(
                to=cast(list[str | EmailAddress], recipients),
                cc=_recipient_values(cc),
                bcc=_recipient_values(bcc),
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                thread_id=original.thread_id,
                in_reply_to=original.headers.get("message-id"),
                references=self._references_for_reply(original),
            )
        )
        payload = await self._request_json(
            "POST",
            "/gmail/v1/users/me/messages/send",
            operation="Gmail reply send",
            json={"message": message_payload, "threadId": original.thread_id},
        )
        return self._send_result_from_payload(payload)

    async def forward(
        self,
        message_id: str,
        to: Iterable[str | EmailAddress] | str | EmailAddress,
        body_text: str,
        *,
        cc: Iterable[str | EmailAddress] | str | EmailAddress = (),
        bcc: Iterable[str | EmailAddress] | str | EmailAddress = (),
        body_html: str | None = None,
    ) -> GmailSendResult:
        """Forward a normalized original message as a new outbound message."""
        original = await self.get_message(message_id, format="full")
        original_text = original.body_text or html_to_text(original.body_html or "")
        separator = "\n\n---------- Forwarded message ----------\n"
        forwarded_body = f"{body_text}{separator}{original_text}" if original_text else body_text
        subject = original.subject or ""
        if not subject.casefold().startswith("fwd:"):
            subject = f"Fwd: {subject}" if subject else "Fwd:"
        message_payload = self._compose_message_payload(
            GmailComposeRequest(
                to=_recipient_values(to),
                cc=_recipient_values(cc),
                bcc=_recipient_values(bcc),
                subject=subject,
                body_text=forwarded_body,
                body_html=body_html,
            )
        )
        payload = await self._request_json(
            "POST",
            "/gmail/v1/users/me/messages/send",
            operation="Gmail forward send",
            json={"message": message_payload},
        )
        return self._send_result_from_payload(payload)

    async def archive(self, message_id: str) -> GmailMutationResult:
        """Remove the Inbox label without deleting the message."""
        return await self._modify_labels(
            message_id, remove_label_ids=["INBOX"], operation="archive"
        )

    async def trash(self, message_id: str) -> GmailMutationResult:
        """Move a message to the Gmail trash."""
        identifier = _validate_identifier(message_id, "message identifier")
        payload = await self._request_json(
            "POST",
            f"/gmail/v1/users/me/messages/{quote(identifier, safe='')}/trash",
            operation="Gmail message trash",
            json={},
            retryable=True,
        )
        return self._mutation_from_payload(payload, identifier, "trash")

    async def add_label(
        self, message_id: str, label_ids: Iterable[str] | str
    ) -> GmailMutationResult:
        """Add one or more existing Gmail labels."""
        return await self._modify_labels(
            message_id,
            add_label_ids=[label_ids] if isinstance(label_ids, str) else list(label_ids),
            operation="add_label",
        )

    async def remove_label(
        self, message_id: str, label_ids: Iterable[str] | str
    ) -> GmailMutationResult:
        """Remove one or more Gmail labels."""
        return await self._modify_labels(
            message_id,
            remove_label_ids=[label_ids] if isinstance(label_ids, str) else list(label_ids),
            operation="remove_label",
        )

    async def _modify_labels(
        self,
        message_id: str,
        *,
        add_label_ids: Iterable[str] | str = (),
        remove_label_ids: Iterable[str] | str = (),
        operation: str,
    ) -> GmailMutationResult:
        identifier = _validate_identifier(message_id, "message identifier")
        additions = self._normalize_label_ids(add_label_ids)
        removals = self._normalize_label_ids(remove_label_ids)
        if not additions and not removals:
            raise DomainValidationError("At least one Gmail label mutation is required.")
        payload = await self._request_json(
            "POST",
            f"/gmail/v1/users/me/messages/{quote(identifier, safe='')}/modify",
            operation=f"Gmail message {operation}",
            json={"addLabelIds": additions, "removeLabelIds": removals},
            retryable=True,
        )
        return self._mutation_from_payload(payload, identifier, operation)

    @staticmethod
    def _normalize_label_ids(values: Iterable[str] | str) -> list[str]:
        normalized: list[str] = []
        for value in [values] if isinstance(values, str) else values:
            item = value.strip()
            if not item:
                raise DomainValidationError("Gmail label IDs cannot be blank.")
            if "/" in item or "?" in item or "#" in item:
                raise DomainValidationError("Gmail label IDs contain an invalid character.")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @staticmethod
    def _compose_message_payload(request: GmailComposeRequest) -> dict[str, Any]:
        raw = _compose_raw_message(
            to=request.to,
            subject=request.subject,
            body_text=request.body_text,
            cc=request.cc,
            bcc=request.bcc,
            body_html=request.body_html,
            in_reply_to=request.in_reply_to,
            references=request.references,
        )
        payload: dict[str, Any] = {"raw": raw}
        if request.thread_id:
            payload["threadId"] = _validate_identifier(request.thread_id, "thread identifier")
        return payload

    @staticmethod
    def _draft_from_payload(payload: object) -> GmailDraft:
        data = require_object(payload, "Gmail draft response")
        identifier = data.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ExternalServiceError(
                "Google returned an invalid Gmail draft.", service_name="gmail"
            )
        message = data.get("message")
        return GmailDraft(
            id=identifier,
            message=_message_from_payload(message) if isinstance(message, dict) else None,
        )

    @staticmethod
    def _send_result_from_payload(payload: object) -> GmailSendResult:
        data = require_object(payload, "Gmail send response")
        identifier = data.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ExternalServiceError(
                "Google returned an invalid Gmail send result.", service_name="gmail"
            )
        return GmailSendResult(
            id=identifier,
            thread_id=data.get("threadId") if isinstance(data.get("threadId"), str) else None,
            label_ids=[item for item in data.get("labelIds", []) if isinstance(item, str)],
        )

    @staticmethod
    def _mutation_from_payload(
        payload: object, fallback_id: str, operation: str
    ) -> GmailMutationResult:
        data = require_object(payload, f"Gmail {operation} response")
        raw_identifier = data.get("id")
        identifier = (
            raw_identifier
            if isinstance(raw_identifier, str) and raw_identifier.strip()
            else fallback_id
        )
        return GmailMutationResult(
            resource_id=identifier,
            operation=operation,
            thread_id=data.get("threadId") if isinstance(data.get("threadId"), str) else None,
            label_ids=[item for item in data.get("labelIds", []) if isinstance(item, str)],
        )

    @staticmethod
    def _references_for_reply(original: GmailMessage) -> str | None:
        values = [original.headers.get("references"), original.headers.get("message-id")]
        normalized = " ".join(value.strip() for value in values if value and value.strip())
        return normalized or None


__all__ = [
    "GMAIL_MODIFY_SCOPE",
    "GMAIL_READ_SCOPE",
    "GmailAdapter",
    "html_to_text",
]
