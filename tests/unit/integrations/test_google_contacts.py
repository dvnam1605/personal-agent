"""Deterministic tests for the Google Contacts (People API) adapter."""

from typing import Any

import httpx
import pytest

from app.domain.errors import ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.integrations.google_contacts import CONTACTS_MAX_PAGE_SIZE, ContactsAdapter
from app.services.google_auth import GoogleApiClient


class FakeGoogleTransport:
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


def _client(transport: FakeGoogleTransport) -> GoogleApiClient:
    return GoogleApiClient(
        _access_token="test-token", base_url="https://google.test", transport=transport
    )


def _json(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _person(resource_name: str = "people/1", email: str | None = "nam@example.com") -> dict:
    person: dict[str, Any] = {
        "resourceName": resource_name,
        "names": [{"displayName": "Nam Nguyen", "givenName": "Nam", "familyName": "Nguyen"}],
        "emailAddresses": [{"value": email, "type": "work"}] if email else [],
        "phoneNumbers": [{"value": "+8490000000", "type": "mobile"}],
        "organizations": [{"name": "Example Corp"}],
        "etag": "etag-1",
    }
    return person


@pytest.mark.asyncio
async def test_search_normalizes_contacts_and_deduplicates() -> None:
    transport = FakeGoogleTransport(
        [
            _json(
                200,
                {
                    "results": [
                        {"person": _person("people/1")},
                        {"person": _person("people/1")},  # duplicate resource name
                        {},  # result without a person is skipped
                        {"person": _person("people/2", None)},
                    ],
                    "nextPageToken": "token-2",
                    "totalItems": 3,
                },
            )
        ]
    )
    adapter = ContactsAdapter(_client(transport))

    page = await adapter.search("Nam", page_size=10)

    assert [contact.resource_name for contact in page.items] == ["people/1", "people/2"]
    assert page.next_page_token == "token-2"
    assert page.result_size_estimate == 3
    first = page.items[0]
    assert first.display_name == "Nam Nguyen"
    assert first.emails[0].value == "nam@example.com"
    assert first.phones[0].value == "+8490000000"
    assert first.organizations == ["Example Corp"]
    method, url, kwargs = transport.calls[0]
    assert method == "GET"
    assert url.endswith("/v1/people:searchContacts")
    assert kwargs["params"]["query"] == "Nam"
    assert kwargs["params"]["pageSize"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_query", ["", "   "])
async def test_search_rejects_blank_query(bad_query: str) -> None:
    adapter = ContactsAdapter(_client(FakeGoogleTransport([])))
    with pytest.raises(DomainValidationError):
        await adapter.search(bad_query)


def test_page_size_bounds_match_people_api_limit() -> None:
    assert CONTACTS_MAX_PAGE_SIZE == 30
    adapter = ContactsAdapter(_client(FakeGoogleTransport([])))
    import asyncio

    async def probe(page_size: int):
        try:
            await adapter.search("x", page_size=page_size)
        except DomainValidationError:
            return False
        return True

    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(probe(0)) is False
        assert loop.run_until_complete(probe(31)) is False
    finally:
        loop.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource_name",
    ["", "   ", "otherPeople/1", "people/?id=1", "people/#frag", "people/../secret"],
)
async def test_get_rejects_invalid_resource_names(resource_name: str) -> None:
    adapter = ContactsAdapter(_client(FakeGoogleTransport([])))
    with pytest.raises(DomainValidationError):
        await adapter.get(resource_name)


@pytest.mark.asyncio
async def test_get_fetches_person_fields() -> None:
    transport = FakeGoogleTransport([_json(200, _person("people/9"))])
    adapter = ContactsAdapter(_client(transport))

    contact = await adapter.get("people/9")

    assert contact.resource_name == "people/9"
    assert contact.etag == "etag-1"
    method, url, kwargs = transport.calls[0]
    assert "/v1/people/9" in url
    request_params = kwargs["params"]
    assert request_params["personFields"].startswith("names,")


@pytest.mark.asyncio
async def test_missing_resource_name_raises_provider_error() -> None:
    broken = _person()
    del broken["resourceName"]
    transport = FakeGoogleTransport([_json(200, {"results": [{"person": broken}]})])
    adapter = ContactsAdapter(_client(transport))

    with pytest.raises(ExternalServiceError):
        await adapter.search("Nam")
