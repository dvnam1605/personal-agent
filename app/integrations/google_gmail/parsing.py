"""Gmail MIME decoding, message extraction, formatting, and address validation."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formataddr, getaddresses
from html.parser import HTMLParser
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models import (
    EmailAddress,
    GmailMessage,
    GmailMessageSummary,
    GmailThreadSummary,
)
from app.integrations.google_common import require_object

GMAIL_MAX_PAGE_SIZE = 500


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


__all__ = [
    "GMAIL_MAX_PAGE_SIZE",
    "_HtmlTextExtractor",
    "_compose_raw_message",
    "_decode_base64_bytes",
    "_decode_body",
    "_extract_body",
    "_first_address",
    "_header_map",
    "_message_from_payload",
    "_normalize_outgoing_addresses",
    "_parse_addresses",
    "_parse_internal_date",
    "_raw_to_payload",
    "_recipient_values",
    "_summary_from_payload",
    "_thread_summary_from_payload",
    "_validate_identifier",
    "_validate_page_size",
    "html_to_text",
]
