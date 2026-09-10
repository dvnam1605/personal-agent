"""Calendar service tests for deterministic availability behavior."""

from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import ExternalServiceError
from app.integrations.google_calendar import CALENDAR_READONLY_SCOPE
from app.services.google.auth import GoogleApiClient, GoogleOAuthService
from app.services.google.calendar import CalendarService


class FakeGoogleTransport:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append(("POST", url, kwargs))
        return self.response

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        raise AssertionError(f"Unexpected GET request: {url} {kwargs}")


class FakeOAuthService:
    def __init__(self, transport: FakeGoogleTransport) -> None:
        self.transport = transport
        self.required_scopes: list[str] | None = None

    async def create_client(
        self,
        session: object,
        user_id: str,
        required_scopes: list[str],
        transport: FakeGoogleTransport | None = None,
    ) -> GoogleApiClient:
        del session, user_id
        self.required_scopes = list(required_scopes)
        return GoogleApiClient(
            _access_token="test-access-token",
            base_url="https://google.test",
            transport=transport or self.transport,
        )


@pytest.mark.asyncio
async def test_calendar_service_for_user_accepts_least_readonly_scope() -> None:
    transport = FakeGoogleTransport(httpx.Response(200, json={"calendars": {}}))
    oauth_service = FakeOAuthService(transport)

    await CalendarService.for_user(
        cast(GoogleOAuthService, oauth_service),
        cast(AsyncSession, object()),
        "user-1",
        required_scopes=[CALENDAR_READONLY_SCOPE],
    )

    assert oauth_service.required_scopes == [CALENDAR_READONLY_SCOPE]


@pytest.mark.asyncio
async def test_calendar_service_finds_slots_from_provider_busy_intervals() -> None:
    transport = FakeGoogleTransport(
        httpx.Response(
            200,
            json={
                "calendars": {
                    "primary": {
                        "busy": [
                            {
                                "start": "2026-08-20T09:30:00+07:00",
                                "end": "2026-08-20T10:30:00+07:00",
                            }
                        ]
                    }
                }
            },
        )
    )
    client = GoogleApiClient(
        _access_token="test-access-token",
        base_url="https://google.test",
        transport=transport,
    )
    service = CalendarService.from_client(client)

    slots = await service.find_free_slots(
        datetime(2026, 8, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        datetime(2026, 8, 20, 12, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        30,
        time_zone="Asia/Ho_Chi_Minh",
    )

    assert [slot.start for slot in slots] == [
        datetime(2026, 8, 20, 2, tzinfo=UTC),
        datetime(2026, 8, 20, 3, 30, tzinfo=UTC),
        datetime(2026, 8, 20, 4, tzinfo=UTC),
        datetime(2026, 8, 20, 4, 30, tzinfo=UTC),
    ]
    assert transport.calls[0][2]["json"]["timeZone"] == "Asia/Ho_Chi_Minh"


@pytest.mark.asyncio
async def test_calendar_service_fails_closed_on_per_calendar_free_busy_error() -> None:
    transport = FakeGoogleTransport(
        httpx.Response(
            200,
            json={
                "calendars": {
                    "primary": {
                        "busy": [],
                        "errors": [{"reason": "notFound", "message": "Calendar was not found."}],
                    }
                }
            },
        )
    )
    client = GoogleApiClient(
        _access_token="test-access-token",
        base_url="https://google.test",
        transport=transport,
    )
    service = CalendarService.from_client(client)

    with pytest.raises(ExternalServiceError) as error:
        await service.find_free_slots(
            datetime(2026, 8, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
            datetime(2026, 8, 20, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
            30,
            time_zone="Asia/Ho_Chi_Minh",
        )

    assert error.value.details["calendar_errors"] == {
        "primary": ["notFound: Calendar was not found."]
    }
