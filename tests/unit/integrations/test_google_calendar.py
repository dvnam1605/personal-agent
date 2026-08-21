"""Deterministic P7 tests for Google Calendar adapters and service arithmetic."""

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.domain.errors import ExternalServiceError, ValidationError
from app.domain.models import (
    CalendarAttendee,
    CalendarBusyInterval,
    CalendarEvent,
    CalendarEventRequest,
    CalendarEventTime,
    CalendarEventUpdate,
)
from app.integrations.google_calendar import CalendarAdapter
from app.integrations.google_common import RetryPolicy
from app.services.calendar import (
    busy_intervals_from_events,
    find_deterministic_free_slots,
)
from app.services.google_auth import GoogleApiClient


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

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("PATCH", url, **kwargs)

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


def _event_payload(
    *,
    event_id: str = "event-1",
    start: str = "2026-08-20T09:00:00+07:00",
    end: str = "2026-08-20T10:00:00+07:00",
    all_day: bool = False,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "etag": '"event-etag-1"',
        "status": "confirmed",
        "summary": "Planning",
        "description": "Discuss roadmap",
        "location": "Room 1",
        "start": {"date": start}
        if all_day
        else {"dateTime": start, "timeZone": "Asia/Ho_Chi_Minh"},
        "end": {"date": end} if all_day else {"dateTime": end, "timeZone": "Asia/Ho_Chi_Minh"},
        "attendees": [
            {
                "email": "Bob@example.com",
                "displayName": "Bob",
                "responseStatus": "accepted",
            }
        ],
        "created": "2026-08-19T01:00:00Z",
        "updated": "2026-08-19T02:00:00Z",
    }


@pytest.mark.asyncio
async def test_calendar_list_normalizes_timed_and_all_day_events_and_timezone_bounds() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(
                200,
                {
                    "items": [
                        _event_payload(),
                        _event_payload(
                            event_id="all-day-1",
                            start="2026-08-21",
                            end="2026-08-23",
                            all_day=True,
                        ),
                    ],
                    "nextPageToken": "next-page",
                    "resultSizeEstimate": 2,
                },
            )
        ]
    )
    adapter = CalendarAdapter(_client(transport))

    page = await adapter.list_events(
        time_min=datetime(2026, 8, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        time_max=datetime(2026, 8, 20, 18, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        time_zone="Asia/Ho_Chi_Minh",
        page_size=25,
    )

    assert page.next_page_token == "next-page"
    assert page.result_size_estimate == 2
    assert page.items[0].start.datetime_value == datetime(2026, 8, 20, 2, tzinfo=UTC)
    assert page.items[0].attendees[0].email == "bob@example.com"
    assert page.items[1].start.date_value == date(2026, 8, 21)
    assert page.items[1].end.date_value == date(2026, 8, 23)
    params = transport.calls[0][2]["params"]
    assert params["timeMin"] == "2026-08-20T02:00:00Z"
    assert params["timeMax"] == "2026-08-20T11:00:00Z"
    assert params["timeZone"] == "Asia/Ho_Chi_Minh"
    assert params["maxResults"] == 25
    assert params["orderBy"] == "startTime"


@pytest.mark.asyncio
async def test_calendar_search_and_free_busy_preserve_query_tokens_and_busy_ranges() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(200, {"items": [], "nextPageToken": "search-next"}),
            _json_response(
                200,
                {
                    "calendars": {
                        "primary": {
                            "busy": [
                                {
                                    "start": "2026-08-20T09:30:00+07:00",
                                    "end": "2026-08-20T10:30:00+07:00",
                                }
                            ],
                            "errors": [],
                        }
                    }
                },
            ),
        ]
    )
    adapter = CalendarAdapter(_client(transport))
    page = await adapter.search_events("roadmap", page_token="page-1")
    free_busy = await adapter.get_free_busy(
        datetime(2026, 8, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        datetime(2026, 8, 20, 12, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        time_zone="Asia/Ho_Chi_Minh",
    )

    assert page.next_page_token == "search-next"
    assert transport.calls[0][2]["params"]["q"] == "roadmap"
    assert transport.calls[0][2]["params"]["pageToken"] == "page-1"
    busy = free_busy.calendars["primary"].busy[0]
    assert busy.start == datetime(2026, 8, 20, 2, 30, tzinfo=UTC)
    assert busy.end == datetime(2026, 8, 20, 3, 30, tzinfo=UTC)
    assert transport.calls[1][0] == "POST"
    assert transport.calls[1][1].endswith("/calendar/v3/freeBusy")
    assert transport.calls[1][2]["json"]["items"] == [{"id": "primary"}]


@pytest.mark.asyncio
async def test_calendar_free_busy_fails_closed_when_requested_calendar_is_missing() -> None:
    adapter = CalendarAdapter(
        _client(FakeGoogleTransport([_json_response(200, {"calendars": {}})]))
    )

    result = await adapter.get_free_busy(
        datetime(2026, 8, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        datetime(2026, 8, 20, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        calendar_ids=["shared-calendar"],
    )

    assert result.calendars["shared-calendar"].busy == []
    assert result.calendars["shared-calendar"].errors == [
        "Calendar was not returned by Google free/busy response."
    ]


@pytest.mark.asyncio
async def test_calendar_free_busy_rejects_malformed_busy_interval() -> None:
    adapter = CalendarAdapter(
        _client(
            FakeGoogleTransport(
                [
                    _json_response(
                        200,
                        {"calendars": {"primary": {"busy": [{"start": "missing-end"}]}}},
                    )
                ]
            )
        )
    )

    with pytest.raises(ExternalServiceError, match="invalid busy intervals"):
        await adapter.get_free_busy(
            datetime(2026, 8, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
            datetime(2026, 8, 20, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        )


@pytest.mark.asyncio
async def test_calendar_free_busy_retries_transient_read_failure() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(503, {"error": "temporary"}),
            _json_response(200, {"calendars": {"primary": {"busy": []}}}),
        ]
    )
    adapter = CalendarAdapter(
        _client(transport),
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0, max_delay_seconds=0),
    )

    result = await adapter.get_free_busy(
        datetime(2026, 8, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        datetime(2026, 8, 20, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    )

    assert result.calendars["primary"].busy == []
    assert len(transport.calls) == 2
    assert adapter.last_retry_count == 1


@pytest.mark.asyncio
async def test_calendar_create_update_delete_are_typed_and_not_retried_as_external_mutations() -> (
    None
):
    payload = _event_payload(event_id="event-2")
    transport = FakeGoogleTransport(
        [
            _json_response(200, payload),
            _json_response(200, {**payload, "summary": "Updated"}),
            httpx.Response(204),
        ]
    )
    adapter = CalendarAdapter(
        _client(transport),
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0, max_delay_seconds=0),
    )
    request = CalendarEventRequest.from_values(
        summary="Planning",
        start="2026-08-20T09:00:00+07:00",
        end="2026-08-20T10:00:00+07:00",
        time_zone="Asia/Ho_Chi_Minh",
        attendees=["bob@example.com"],
    )

    created = await adapter.create_event(request, send_updates="none")
    updated = await adapter.update_event(
        created.id,
        CalendarEventUpdate.from_values(summary="Updated"),
        send_updates="externalOnly",
    )
    deleted = await adapter.delete_event(created.id, send_updates="none")

    assert created.id == "event-2"
    assert updated.summary == "Updated"
    assert deleted.deleted is True
    assert [call[0] for call in transport.calls] == ["POST", "PATCH", "DELETE"]
    assert transport.calls[0][2]["params"]["sendUpdates"] == "none"
    assert transport.calls[1][2]["params"]["sendUpdates"] == "externalOnly"
    assert transport.calls[0][2]["json"]["start"]["dateTime"] == "2026-08-20T09:00:00+07:00"


@pytest.mark.asyncio
async def test_calendar_create_is_not_retried_after_transient_provider_error() -> None:
    transport = FakeGoogleTransport(
        [_json_response(503, {"error": "temporary"}), _json_response(200, _event_payload())]
    )
    adapter = CalendarAdapter(
        _client(transport),
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0, max_delay_seconds=0),
    )
    request = CalendarEventRequest.from_values(
        start="2026-08-20T09:00:00+07:00",
        end="2026-08-20T10:00:00+07:00",
        time_zone="Asia/Ho_Chi_Minh",
    )

    with pytest.raises(ExternalServiceError):
        await adapter.create_event(request)

    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_calendar_attendee_mutations_fetch_once_and_patch_without_duplicates() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(200, {**_event_payload(), "attendees": []}),
            _json_response(200, {**_event_payload(), "attendees": [{"email": "new@example.com"}]}),
            _json_response(200, {**_event_payload(), "attendees": [{"email": "new@example.com"}]}),
            _json_response(200, {**_event_payload(), "attendees": []}),
        ]
    )
    adapter = CalendarAdapter(_client(transport))

    added = await adapter.add_attendee(
        "event-1", CalendarAttendee(email="new@example.com"), send_updates="none"
    )
    removed = await adapter.remove_attendee("event-1", "new@example.com", send_updates="none")

    assert [call[0] for call in transport.calls] == ["GET", "PATCH", "GET", "PATCH"]
    assert added.attendees[0].email == "new@example.com"
    assert removed.attendees == []
    assert transport.calls[1][2]["json"]["attendees"] == [{"email": "new@example.com"}]
    assert transport.calls[3][2]["json"]["attendees"] == []
    assert transport.calls[1][2]["headers"]["If-Match"] == '"event-etag-1"'
    assert transport.calls[3][2]["headers"]["If-Match"] == '"event-etag-1"'


@pytest.mark.asyncio
async def test_calendar_attendee_patch_surfaces_precondition_failure_for_review() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(200, {**_event_payload(), "attendees": []}),
            _json_response(412, {"error": {"reason": "conditionNotMet"}}),
        ]
    )
    adapter = CalendarAdapter(_client(transport))

    with pytest.raises(ValidationError) as error:
        await adapter.add_attendee(
            "event-1", CalendarAttendee(email="new@example.com"), send_updates="none"
        )

    assert error.value.details["status_code"] == 412
    assert transport.calls[1][2]["headers"]["If-Match"] == '"event-etag-1"'


@pytest.mark.asyncio
async def test_calendar_attendee_patch_aborts_when_event_has_no_etag() -> None:
    transport = FakeGoogleTransport(
        [
            _json_response(200, {**_event_payload(), "etag": None, "attendees": []}),
        ]
    )
    adapter = CalendarAdapter(_client(transport))

    with pytest.raises(ExternalServiceError, match="did not include an ETag"):
        await adapter.add_attendee(
            "event-1", CalendarAttendee(email="new@example.com"), send_updates="none"
        )

    assert len(transport.calls) == 1


def test_calendar_slot_finder_is_deterministic_for_30_45_and_60_minute_slots() -> None:
    window_start = datetime(2026, 8, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    window_end = datetime(2026, 8, 20, 12, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert len(find_deterministic_free_slots([], window_start, window_end, 30)) == 6
    assert len(find_deterministic_free_slots([], window_start, window_end, 45)) == 4
    assert len(find_deterministic_free_slots([], window_start, window_end, 60)) == 3


def test_calendar_slot_finder_merges_overlapping_meetings_and_respects_all_day_events() -> None:
    window_start = datetime(2026, 8, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    window_end = datetime(2026, 8, 20, 13, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    overlapping = [
        CalendarBusyInterval(
            start=datetime(2026, 8, 20, 2, 30, tzinfo=UTC),
            end=datetime(2026, 8, 20, 3, 30, tzinfo=UTC),
        ),
        CalendarBusyInterval(
            start=datetime(2026, 8, 20, 3, tzinfo=UTC),
            end=datetime(2026, 8, 20, 4, tzinfo=UTC),
        ),
    ]
    slots = find_deterministic_free_slots(
        overlapping, window_start, window_end, 30, time_zone="Asia/Ho_Chi_Minh"
    )
    assert [slot.start.hour for slot in slots] == [2, 4, 4, 5, 5]

    all_day = CalendarEvent(
        id="all-day",
        start=CalendarEventTime(
            value=date(2026, 8, 20), all_day=True, time_zone="Asia/Ho_Chi_Minh"
        ),
        end=CalendarEventTime(value=date(2026, 8, 22), all_day=True, time_zone="Asia/Ho_Chi_Minh"),
    )
    all_day_busy = busy_intervals_from_events([all_day], time_zone="Asia/Ho_Chi_Minh")
    assert all_day_busy[0].start == datetime(2026, 8, 19, 17, tzinfo=UTC)
    assert all_day_busy[0].end == datetime(2026, 8, 21, 17, tzinfo=UTC)
    assert not find_deterministic_free_slots(
        all_day_busy,
        datetime(2026, 8, 20, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        datetime(2026, 8, 20, 17, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        60,
        time_zone="Asia/Ho_Chi_Minh",
    )


@pytest.mark.asyncio
async def test_calendar_request_validation_rejects_naive_timed_values_and_bad_page_sizes() -> None:
    with pytest.raises(ValueError, match="Timezone-aware"):
        CalendarEventTime(value=datetime(2026, 8, 20, 9), all_day=False)

    transport = FakeGoogleTransport([])
    adapter = CalendarAdapter(_client(transport))
    with pytest.raises(ValidationError, match="between 1 and 2500"):
        await adapter.list_events(page_size=2501)
