"""Deterministic P6 tests for Gmail and Google Contacts adapters/tools."""

import base64
from typing import Any

import httpx
import pytest

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.errors import AuthenticationError, ExternalServiceError, ValidationError
from app.domain.models import ToolContext, ToolInput
from app.integrations.google_common import RetryPolicy
from app.integrations.google_contacts import ContactsAdapter
from app.integrations.google_gmail import GmailAdapter, html_to_text
from app.services.communication import CommunicationService
from app.services.google_auth import GoogleApiClient
from app.tools import GoogleCommunicationTools, build_communication_tool_registry


class FakeGoogleTransport:
    """Queue-based authorized transport; no request reaches Google."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _next(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("Fake provider response queue is empty.")
        return self.responses.pop(0)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("DELETE", url, **kwargs)


def _client(transport: FakeGoogleTransport) -> GoogleApiClient:
    return GoogleApiClient(
        _access_token="test-access-token",
        base_url="https://google.test",
        transport=transport,
    )


def _json_response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _message_payload(
    *,
    message_id: str = "message-1",
    thread_id: str = "thread-1",
    html: str | None = None,
    plain: str | None = None,
    sender: str = "Alice Example <alice@example.com>",
    subject: str = "Hello",
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if plain is not None:
        parts.append(
            {
                "mimeType": "text/plain",
                "body": {"data": _b64(plain)},
            }
        )
    if html is not None:
        parts.append(
            {
                "mimeType": "text/html",
                "body": {"data": _b64(html)},
            }
        )
    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": ["INBOX"],
        "snippet": "Hello",
        "internalDate": "1760000000000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "owner@example.com"},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": "<message-1@example.com>"},
            ],
            "parts": parts,
        },
    }


@pytest.mark.asyncio
async def test_malformed_html_is_normalized_without_tags_or_failure() -> None:
    assert html_to_text("<p>Hello <b>world</b><br><broken>&amp; safe") == "Hello world\n& safe"

    transport = FakeGoogleTransport(
        [_json_response(200, _message_payload(html="<p>Hello <b>world</b><br><broken>&amp; safe"))]
    )
    adapter = GmailAdapter(_client(transport))

    message = await adapter.get_message("message-1")

    assert message.body_text == "Hello world\n& safe"
    assert message.sender is not None
    assert message.sender.email == "alice@example.com"
    assert message.subject == "Hello"


@pytest.mark.asyncio
async def test_gmail_search_retries_transient_provider_error_and_preserves_page_token() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(503, {"error": "temporary"}),
            _json_response(
                200,
                {
                    "messages": [{"id": "m1", "threadId": "t1"}],
                    "nextPageToken": "next-1",
                    "resultSizeEstimate": 3,
                },
            ),
        ]
    )
    adapter = GmailAdapter(
        _client(transport),
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0, max_delay_seconds=0),
        sleep=lambda _delay: _completed_sleep(),
    )

    page = await adapter.search_messages("from:alice", page_size=2)

    assert [item.id for item in page.items] == ["m1"]
    assert page.next_page_token == "next-1"
    assert adapter.last_retry_count == 1
    assert len(transport.calls) == 2
    assert transport.calls[-1][2]["params"]["q"] == "from:alice"
    assert transport.calls[-1][2]["params"]["maxResults"] == 2


@pytest.mark.asyncio
async def test_non_idempotent_send_is_not_retried_after_transient_provider_error() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(503, {"error": "temporary"}),
            _json_response(200, {"id": "must-not-be-used-by-retry"}),
        ]
    )
    adapter = GmailAdapter(
        _client(transport),
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0, max_delay_seconds=0),
        sleep=lambda _delay: _completed_sleep(),
    )

    with pytest.raises(ExternalServiceError):
        await adapter.send_draft("draft-1")

    assert len(transport.calls) == 1


async def _completed_sleep() -> None:
    return None


@pytest.mark.asyncio
async def test_provider_auth_error_is_normalized_without_provider_payload_leakage() -> None:
    transport = FakeGoogleTransport(
        [_json_response(401, {"error": "invalid_token", "access_token": "must-not-leak"})]
    )
    adapter = GmailAdapter(_client(transport))

    with pytest.raises(AuthenticationError) as error:
        await adapter.get_message("message-1")

    assert "must-not-leak" not in str(error.value)
    assert "must-not-leak" not in str(error.value.details)
    assert error.value.details["status_code"] == 401


def _person(resource_name: str, display_name: str, email: str) -> dict[str, Any]:
    return {
        "resourceName": resource_name,
        "names": [{"displayName": display_name, "givenName": display_name.split()[0]}],
        "emailAddresses": [{"value": email, "type": "home"}],
    }


@pytest.mark.asyncio
async def test_contact_resolution_is_exact_and_ambiguous_results_never_guess() -> None:
    exact_transport = FakeGoogleTransport(
        [
            _json_response(
                200,
                {
                    "results": [{"person": _person("people/1", "Alice Example", "alice@example.com")}],
                },
            )
        ]
    )
    exact_service = CommunicationService.from_client(_client(exact_transport))
    exact = await exact_service.resolve_person("alice@example.com")

    assert exact.status.value == "exact"
    assert exact.contact is not None
    assert exact.contact.display_name == "Alice Example"

    ambiguous_transport = FakeGoogleTransport(
        [
            _json_response(
                200,
                {
                    "results": [
                        {"person": _person("people/1", "Alice Example", "alice@example.com")},
                        {"person": _person("people/2", "Alice Smith", "alice.smith@example.com")},
                    ],
                },
            )
        ]
    )
    ambiguous_service = CommunicationService.from_client(_client(ambiguous_transport))
    ambiguous = await ambiguous_service.resolve_person("Alice")

    assert ambiguous.status.value == "ambiguous"
    assert ambiguous.contact is None
    assert len(ambiguous.candidates) == 2


@pytest.mark.asyncio
async def test_contact_search_pagination_is_consumed_by_resolution() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(
                200,
                {
                    "results": [{"person": _person("people/1", "Other Person", "other@example.com")}],
                    "nextPageToken": "contacts-next",
                },
            ),
            _json_response(
                200,
                {"results": [{"person": _person("people/2", "Target Person", "target@example.com")}]},
            ),
        ]
    )
    service = CommunicationService.from_client(_client(transport))

    result = await service.resolve_person("target@example.com")

    assert result.status.value == "exact"
    assert result.contact is not None
    assert result.contact.email_values == ("target@example.com",)
    assert len(transport.calls) == 2
    assert transport.calls[1][2]["params"]["pageToken"] == "contacts-next"


@pytest.mark.asyncio
async def test_contacts_page_size_respects_people_api_contract() -> None:
    transport = FakeGoogleTransport([])
    adapter = ContactsAdapter(_client(transport))

    with pytest.raises(ValidationError, match="between 1 and 30"):
        await adapter.search("Alice", page_size=31)

    assert not transport.calls


@pytest.mark.asyncio
async def test_create_draft_uses_provider_raw_message_and_tool_registry_classifies_writes() -> None:
    transport = FakeGoogleTransport([_json_response(200, {"id": "draft-1"})])
    adapter = GmailAdapter(_client(transport))

    draft = await adapter.create_draft(["Bob@example.com"], "Subject", "Hello Bob")

    assert draft.id == "draft-1"
    request_json = transport.calls[0][2]["json"]
    raw = request_json["message"]["raw"]
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
    assert "bob@example.com" in decoded
    assert "Subject: Subject" in decoded
    assert "Hello Bob" in decoded

    registry = build_communication_tool_registry()
    definitions = {definition.name: definition for definition in registry.list()}
    assert len(definitions) == 17
    assert definitions["gmail.send_draft"].action_class == ActionClass.EXTERNAL_COMMUNICATION
    assert definitions["gmail.send_draft"].risk_level == ActionRiskLevel.HIGH_IMPACT_WRITE
    assert definitions["gmail.trash"].action_class == ActionClass.DESTRUCTIVE
    assert definitions["gmail.search_messages"].action_class == ActionClass.READ


@pytest.mark.asyncio
async def test_all_gmail_write_operations_use_typed_provider_requests() -> None:
    message = _message_payload(plain="Original body")
    transport = FakeGoogleTransport(
        [
            _json_response(200, {"id": "draft-2"}),
            httpx.Response(204),
            _json_response(200, {"id": "sent-1", "threadId": "thread-1"}),
            _json_response(200, message),
            _json_response(200, {"id": "reply-1", "threadId": "thread-1"}),
            _json_response(200, message),
            _json_response(200, {"id": "forward-1", "threadId": "thread-2"}),
            _json_response(200, {"id": "message-1", "threadId": "thread-1", "labelIds": []}),
            _json_response(200, {"id": "message-1", "threadId": "thread-1", "labelIds": ["TRASH"]}),
            _json_response(200, {"id": "message-1", "threadId": "thread-1", "labelIds": ["STARRED"]}),
            _json_response(200, {"id": "message-1", "threadId": "thread-1", "labelIds": []}),
        ]
    )
    adapter = GmailAdapter(_client(transport))

    await adapter.update_draft("draft-1", ["bob@example.com"], "Updated", "Body")
    await adapter.delete_draft("draft-1")
    await adapter.send_draft("draft-1")
    await adapter.reply("message-1", "Reply")
    await adapter.forward("message-1", ["bob@example.com"], "Forward")
    await adapter.archive("message-1")
    await adapter.trash("message-1")
    await adapter.add_label("message-1", ["STARRED"])
    await adapter.remove_label("message-1", ["STARRED"])

    assert [call[0] for call in transport.calls] == [
        "PUT",
        "DELETE",
        "POST",
        "GET",
        "POST",
        "GET",
        "POST",
        "POST",
        "POST",
        "POST",
        "POST",
    ]
    assert transport.calls[0][2]["json"]["message"]["raw"]
    assert transport.calls[3][1].endswith("/messages/message-1")
    assert transport.calls[4][1].endswith("/messages/send")
    assert transport.calls[7][1].endswith("/messages/message-1/modify")


@pytest.mark.asyncio
async def test_raw_gmail_format_is_normalized_like_full_messages() -> None:
    raw_email = (
        "From: Alice <alice@example.com>\n"
        "To: owner@example.com\n"
        "Subject: Raw hello\n"
        "Content-Type: text/html; charset=utf-8\n\n"
        "<p>Raw <b>body</b></p>"
    )
    transport = FakeGoogleTransport(
        [
            _json_response(
                200,
                {"id": "raw-1", "threadId": "thread-raw", "raw": _b64(raw_email)},
            )
        ]
    )
    adapter = GmailAdapter(_client(transport))

    normalized = await adapter.get_message("raw-1", format="raw")

    assert normalized.subject == "Raw hello"
    assert normalized.body_text == "Raw body"
    assert normalized.sender is not None
    assert normalized.sender.email == "alice@example.com"


@pytest.mark.asyncio
async def test_read_only_tool_view_cannot_execute_gmail_mutation() -> None:
    transport = FakeGoogleTransport([])
    tools = GoogleCommunicationTools(CommunicationService.from_client(_client(transport)))
    result = await tools.execute(
        ToolInput(tool_name="gmail.trash", arguments={"message_id": "message-1"}),
        ToolContext(run_id="run-1", user_id="user-1", read_only_view=True),
    )

    assert result.success is False
    assert "Read-only" in (result.error or "")
    assert not transport.calls


@pytest.mark.asyncio
async def test_normalized_thread_contains_all_messages() -> None:
    payload = {
        "id": "thread-1",
        "historyId": "h1",
        "snippet": "thread",
        "messages": [
            _message_payload(message_id="m1", thread_id="thread-1", plain="first"),
            _message_payload(message_id="m2", thread_id="thread-1", plain="second"),
        ],
    }
    transport = FakeGoogleTransport([_json_response(200, payload)])
    adapter = GmailAdapter(_client(transport))

    thread = await adapter.get_thread("thread-1")

    assert thread.id == "thread-1"
    assert [message.id for message in thread.messages] == ["m1", "m2"]
    assert thread.message_count == 2
