"""Deterministic Gmail API adapter.

This module owns Google wire formats, MIME decoding, pagination, and provider
error boundaries.  Callers receive only provider-neutral communication models.
"""

import base64
import binascii
import re
from collections.abc import Iterable
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formataddr, getaddresses
from html.parser import HTMLParser
from typing import Any, Literal, cast
from urllib.parse import quote

from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models import (
    EmailAddress,
    GmailComposeRequest,
    GmailDraft,
    GmailMessage,
    GmailMessagePage,
    GmailMessageSummary,
    GmailMutationResult,
    GmailSendResult,
    GmailThread,
    GmailThreadPage,
    GmailThreadSummary,
)
from app.integrations.google_common import (
    GoogleResourceAdapter,
    require_list,
    require_object,
)

GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
# gmail.modify is intentionally reused as the read scope: it is the least-privilege
# scope that still permits label/archive mutations, and gmail.readonly would break
# every classified write tool. The alias documents that read paths need nothing wider.
GMAIL_READ_SCOPE = GMAIL_MODIFY_SCOPE
GMAIL_MAX_PAGE_SIZE = 500
GMAIL_FORMATS = ("minimal", "full", "raw", "metadata")
GmailFormat = Literal["minimal", "full", "raw", "metadata"]


class _HtmlTextExtractor(HTMLParser):
    """Small tolerant HTML-to-text parser for untrusted email bodies."""

    _ignored_tags = frozenset({"head", "script", "style", "template"})
    _line_break_tags = frozenset({"br", "div", "li", "p", "tr", "blockquote", "hr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in self._ignored_tags:
            self._ignored_depth += 1
        if self._ignored_depth == 0 and normalized in self._line_break_tags:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
        if self._ignored_depth == 0 and normalized in self._line_break_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    """Convert even malformed HTML into compact, readable plain text."""
    parser = _HtmlTextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        # HTMLParser is intentionally tolerant, but malformed character/entity
        # input should not make a communication lookup fail.
        parser.parts.append(value)
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode_body(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding).decode("utf-8", errors="replace")
    except (binascii.Error, UnicodeError) as exc:
        raise ExternalServiceError(
            "Google returned malformed message content.",
            service_name="gmail",
        ) from exc


def _decode_base64_bytes(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except binascii.Error as exc:
        raise ExternalServiceError(
            "Google returned malformed message content.",
            service_name="gmail",
        ) from exc


def _raw_to_payload(value: str) -> dict[str, Any]:
    """Convert Gmail's raw RFC 822 representation into the same payload shape."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(_decode_base64_bytes(value))
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ExternalServiceError(
            "Google returned malformed raw Gmail content.", service_name="gmail"
        ) from exc

    def part_payload(part: Message) -> dict[str, Any]:
        headers = [
            {"name": name, "value": str(header_value)} for name, header_value in part.items()
        ]
        result: dict[str, Any] = {
            "mimeType": part.get_content_type(),
            "headers": headers,
            "body": {},
        }
        if part.is_multipart():
            children = part.get_payload()
            result["parts"] = (
                [
                    part_payload(child)
                    for child in children
                    if isinstance(children, list) and isinstance(child, Message)
                ]
                if isinstance(children, list)
                else []
            )
            return result
        decoded = part.get_payload(decode=True)
        if not isinstance(decoded, bytes):
            raw_payload = part.get_payload()
            decoded = raw_payload.encode("utf-8") if isinstance(raw_payload, str) else b""
        result["body"] = {"data": base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")}
        filename = part.get_filename()
        if filename:
            result["filename"] = filename
        return result

    return part_payload(message)


def _parse_addresses(value: str | None) -> list[EmailAddress]:
    if not value:
        return []
    result: list[EmailAddress] = []
    for display_name, email in getaddresses([value]):
        if not email.strip():
            continue
        try:
            address = EmailAddress(display_name=display_name or None, email=email)
        except PydanticValidationError:
            continue
        if address not in result:
            result.append(address)
    return result


def _first_address(value: str | None) -> EmailAddress | None:
    addresses = _parse_addresses(value)
    return addresses[0] if addresses else None


def _parse_internal_date(value: object) -> Any:
    if value is None:
        return None
    if not isinstance(value, int | str):
        return None
    try:
        from datetime import UTC, datetime

        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _extract_body(payload: dict[str, Any]) -> tuple[str, str | None, bool]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    has_attachments = False

    def visit(part: object) -> None:
        nonlocal has_attachments
        if not isinstance(part, dict):
            return
        mime_type = str(part.get("mimeType") or "").casefold()
        filename = part.get("filename")
        body = part.get("body")
        if isinstance(filename, str) and filename.strip():
            has_attachments = True
        if (
            isinstance(body, dict)
            and body.get("attachmentId")
            and not mime_type.startswith("text/")
        ):
            has_attachments = True
        parts = part.get("parts")
        if isinstance(parts, list) and parts:
            for child in parts:
                visit(child)
            return
        data = _decode_body(body.get("data") if isinstance(body, dict) else None)
        if mime_type == "text/plain":
            plain_parts.append(data)
        elif mime_type == "text/html":
            html_parts.append(data)

    visit(payload)
    body_html = "\n".join(part for part in html_parts if part) or None
    body_text = "\n".join(part for part in plain_parts if part).strip()
    if not body_text and body_html:
        body_text = html_to_text(body_html)
    return body_text, body_html, has_attachments


def _header_map(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    raw_headers = payload.get("headers", [])
    if not isinstance(raw_headers, list):
        return result
    for header in raw_headers:
        if not isinstance(header, dict):
            continue
        name = header.get("name")
        value = header.get("value")
        if isinstance(name, str) and isinstance(value, str) and name.strip():
            result[name.strip().casefold()] = value.strip()
    return result


def _message_from_payload(payload: object) -> GmailMessage:
    data = require_object(payload, "Gmail message normalization")
    message_payload = data.get("payload")
    if not isinstance(message_payload, dict):
        raw_payload = data.get("raw")
        message_payload = _raw_to_payload(raw_payload) if isinstance(raw_payload, str) else {}
    headers = _header_map(message_payload)
    body_text, body_html, has_attachments = _extract_body(message_payload)
    message_id = data.get("id")
    thread_id = data.get("threadId")
    if not isinstance(message_id, str) or not message_id.strip():
        raise ExternalServiceError(
            "Google returned an invalid Gmail message.", service_name="gmail"
        )
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ExternalServiceError(
            "Google returned an invalid Gmail message.", service_name="gmail"
        )
    try:
        return GmailMessage(
            id=message_id,
            thread_id=thread_id,
            label_ids=[item for item in data.get("labelIds", []) if isinstance(item, str)],
            snippet=str(data.get("snippet") or ""),
            internal_date=_parse_internal_date(data.get("internalDate")),
            headers=headers,
            subject=headers.get("subject"),
            sender=_first_address(headers.get("from")),
            reply_to=_first_address(headers.get("reply-to")),
            to=_parse_addresses(headers.get("to")),
            cc=_parse_addresses(headers.get("cc")),
            bcc=_parse_addresses(headers.get("bcc")),
            body_text=body_text,
            body_html=body_html,
            has_attachments=has_attachments,
            size_estimate=data.get("sizeEstimate")
            if isinstance(data.get("sizeEstimate"), int)
            else None,
            history_id=str(data["historyId"]) if data.get("historyId") is not None else None,
        )
    except (PydanticValidationError, TypeError, ValueError) as exc:
        raise ExternalServiceError(
            "Google returned an invalid Gmail message.",
            service_name="gmail",
        ) from exc


def _summary_from_payload(payload: object) -> GmailMessageSummary:
    data = require_object(payload, "Gmail message search")
    message_id = data.get("id")
    thread_id = data.get("threadId")
    if not isinstance(message_id, str) or not message_id.strip():
        raise ExternalServiceError(
            "Google returned an invalid Gmail message result.", service_name="gmail"
        )
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ExternalServiceError(
            "Google returned an invalid Gmail message result.", service_name="gmail"
        )
    try:
        return GmailMessageSummary(
            id=message_id,
            thread_id=thread_id,
            label_ids=[item for item in data.get("labelIds", []) if isinstance(item, str)],
            snippet=str(data.get("snippet") or ""),
            internal_date=_parse_internal_date(data.get("internalDate")),
        )
    except (PydanticValidationError, TypeError, ValueError) as exc:
        raise ExternalServiceError(
            "Google returned an invalid Gmail message result.",
            service_name="gmail",
        ) from exc


def _thread_summary_from_payload(payload: object) -> GmailThreadSummary:
    data = require_object(payload, "Gmail thread search")
    thread_id = data.get("id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ExternalServiceError(
            "Google returned an invalid Gmail thread result.", service_name="gmail"
        )
    try:
        messages = data.get("messages")
        return GmailThreadSummary(
            id=thread_id,
            snippet=str(data.get("snippet") or ""),
            history_id=str(data["historyId"]) if data.get("historyId") is not None else None,
            message_count=len(messages) if isinstance(messages, list) else None,
        )
    except (PydanticValidationError, TypeError, ValueError) as exc:
        raise ExternalServiceError(
            "Google returned an invalid Gmail thread result.",
            service_name="gmail",
        ) from exc


def _validate_identifier(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized or "/" in normalized or "?" in normalized or "#" in normalized:
        raise DomainValidationError(f"Invalid Gmail {label}.")
    return normalized


def _validate_page_size(value: int) -> int:
    if not 1 <= value <= GMAIL_MAX_PAGE_SIZE:
        raise DomainValidationError(
            "Gmail page size must be between 1 and 500.",
            details={"page_size": value},
        )
    return value


def _recipient_values(
    values: Iterable[str | EmailAddress] | str | EmailAddress,
) -> list[str | EmailAddress]:
    if isinstance(values, (str, EmailAddress)):
        return [values]
    return list(values)


def _normalize_outgoing_addresses(
    values: Iterable[str | EmailAddress] | str | EmailAddress,
) -> list[EmailAddress]:
    normalized: list[EmailAddress] = []
    for value in _recipient_values(values):
        if isinstance(value, EmailAddress):
            address = value
        elif isinstance(value, str):
            parsed = _parse_addresses(value)
            if len(parsed) != 1:
                raise DomainValidationError("Each recipient must contain one valid email address.")
            address = parsed[0]
        else:
            raise DomainValidationError("Recipients must be email addresses.")
        if address not in normalized:
            normalized.append(address)
    return normalized


def _compose_raw_message(
    *,
    to: Iterable[str | EmailAddress] | str | EmailAddress,
    subject: str,
    body_text: str,
    cc: Iterable[str | EmailAddress] | str | EmailAddress = (),
    bcc: Iterable[str | EmailAddress] | str | EmailAddress = (),
    body_html: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    recipients = _normalize_outgoing_addresses(to)
    if not recipients:
        raise DomainValidationError("At least one recipient is required.")
    if any("\r" in value or "\n" in value for value in (subject, in_reply_to, references) if value):
        raise DomainValidationError("Email headers cannot contain line breaks.")
    message = EmailMessage()
    message["To"] = ", ".join(
        formataddr((address.display_name or "", address.email)) for address in recipients
    )
    cc_addresses = _normalize_outgoing_addresses(cc)
    if cc_addresses:
        message["Cc"] = ", ".join(
            formataddr((address.display_name or "", address.email)) for address in cc_addresses
        )
    bcc_addresses = _normalize_outgoing_addresses(bcc)
    if bcc_addresses:
        message["Bcc"] = ", ".join(
            formataddr((address.display_name or "", address.email)) for address in bcc_addresses
        )
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content(body_text)
    if body_html is not None:
        message.add_alternative(body_html, subtype="html")
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")


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
