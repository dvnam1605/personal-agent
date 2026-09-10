"""Unit tests for shared Google HTTP adapter behavior: retries, errors, URL building."""

from typing import Any

import httpx
import pytest

from app.domain.errors import (
    AuthenticationError,
    ExternalServiceError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from app.domain.errors import (
    ValidationError as DomainValidationError,
)
from app.integrations.google_common import GoogleResourceAdapter, RetryPolicy
from app.services.google.auth import GoogleApiClient


class FlakyTransport:
    """Transport that raises or returns queued results per method."""

    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls = 0
        self.last: Any = None

    async def _next(self) -> Any:
        self.calls += 1
        if not self.results:
            item = self.last
        else:
            item = self.results.pop(0)
        if isinstance(item, Exception):
            self.last = item
            raise item
        self.last = item
        return item

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next()

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next()

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next()

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        # GoogleApiClient.request delegates to the transport method map.
        handler = getattr(self, method.lower())
        return await handler(url, **kwargs)


class _NoopAdapter(GoogleResourceAdapter):
    async def noop(self) -> Any:
        return None


def _client(transport: FlakyTransport) -> GoogleApiClient:
    return GoogleApiClient(
        _access_token="token", base_url="https://google.test", transport=transport
    )


def test_retry_policy_delay_bounds_and_retry_after() -> None:
    policy = RetryPolicy(initial_delay_seconds=0.1, max_delay_seconds=0.5)
    assert policy.delay_for_retry(1) == pytest.approx(0.1)
    assert policy.delay_for_retry(5) == 0.5  # capped by max delay

    retry_after_response = httpx.Response(429, headers={"Retry-After": "2"})
    assert policy.delay_for_retry(1, retry_after_response) == 0.5  # capped at max
    junk = httpx.Response(429, headers={"Retry-After": "not-a-number"})
    assert policy.delay_for_retry(1, junk) == pytest.approx(0.1)

    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(initial_delay_seconds=-1.0)
    with pytest.raises(ValueError):
        RetryPolicy(initial_delay_seconds=2.0, max_delay_seconds=1.0)


@pytest.mark.asyncio
async def test_request_json_retries_transient_network_error_then_succeeds() -> None:
    transport = FlakyTransport(
        [
            httpx.ConnectError("boom"),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    adapter = _NoopAdapter(_client(transport))
    payload = await adapter._request_json("GET", "/test", operation="probe")
    assert payload == {"ok": True}
    assert adapter.last_retry_count == 1


@pytest.mark.asyncio
async def test_request_json_does_not_retry_post_by_default() -> None:
    transport = FlakyTransport([httpx.ConnectError("boom")])
    adapter = _NoopAdapter(_client(transport))
    with pytest.raises(ExternalServiceError):
        await adapter._request_json("POST", "/test", operation="probe")
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_request_json_maps_provider_status_errors() -> None:
    cases = {
        401: AuthenticationError,
        403: PermissionDeniedError,
        404: NotFoundError,
        429: RateLimitError,
        400: DomainValidationError,
        500: ExternalServiceError,
    }
    for status_code, expected in cases.items():
        transport = FlakyTransport([httpx.Response(status_code, json={})])
        adapter = _NoopAdapter(_client(transport))
        with pytest.raises(expected):
            await adapter._request_json("GET", "/test", operation="probe")


@pytest.mark.asyncio
async def test_request_json_empty_and_invalid_payloads_fail_closed() -> None:
    adapter = _NoopAdapter(_client(FlakyTransport([httpx.Response(204)])))
    with pytest.raises(ExternalServiceError):
        await adapter._request_json("GET", "/test", operation="probe")

    adapter = _NoopAdapter(_client(FlakyTransport([httpx.Response(204)])))
    assert await adapter._request_json("GET", "/test", operation="probe", allow_empty=True) is None

    invalid_json = httpx.Response(200, text="not-json")
    adapter = _NoopAdapter(_client(FlakyTransport([invalid_json])))
    with pytest.raises(ExternalServiceError):
        await adapter._request_json("GET", "/test", operation="probe")


def test_url_builder_prefers_absolute_paths() -> None:
    adapter = _NoopAdapter(_client(FlakyTransport([])))
    assert adapter._url("/v1/x") == "https://google.test/v1/x"
    assert adapter._url("https://other.test/upload") == "https://other.test/upload"


@pytest.mark.asyncio
async def test_operation_retry_collection_scopes_per_logical_operation() -> None:
    adapter = _NoopAdapter(_client(FlakyTransport([])))
    adapter.begin_operation()
    adapter._operation_retry_count.set(3)
    assert adapter.last_operation_retry_count == 3
    adapter.finish_operation()


@pytest.mark.asyncio
async def test_request_bytes_retries_and_maps_errors() -> None:
    transport = FlakyTransport(
        [httpx.ConnectError("drop"), httpx.Response(200, content=b"binary", headers={"x": "1"})]
    )
    adapter = _NoopAdapter(_client(transport))
    body, headers = await adapter._request_bytes("GET", "/file", operation="download")
    assert body == b"binary"
    assert headers["x"] == "1"

    transport = FlakyTransport([httpx.Response(404, json={})])
    adapter = _NoopAdapter(_client(transport))
    with pytest.raises(NotFoundError):
        await adapter._request_bytes("GET", "/file", operation="download")


@pytest.mark.asyncio
async def test_request_raw_post_is_not_retried_and_returns_response() -> None:
    ok = httpx.Response(200, json={"id": "1"})
    transport = FlakyTransport([ok])
    adapter = _NoopAdapter(_client(transport))
    response = await adapter._request_raw(
        "POST",
        "/upload",
        content=b"data",
        headers={"Content-Type": "text/plain"},
        operation="upload",
        retryable=False,
    )
    assert response is ok

    transport = FlakyTransport([httpx.ConnectError("down")])
    adapter = _NoopAdapter(_client(transport))
    with pytest.raises(ExternalServiceError):
        await adapter._request_raw(
            "POST", "/upload", content=b"data", operation="upload", retryable=False
        )


def test_require_helpers_reject_invalid_provider_shapes() -> None:
    from app.integrations.google_common import require_list, require_object

    with pytest.raises(ExternalServiceError):
        require_object(["not", "a", "dict"], "probe")
    with pytest.raises(ExternalServiceError):
        require_list({"files": "not-a-list"}, "files", "probe")
    assert require_list({}, "missing", "probe") == []
    assert require_object({"k": 1}, "probe") == {"k": 1}
