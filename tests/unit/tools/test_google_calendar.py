"""P7 Calendar tool registry and execution tests."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.models import ToolContext, ToolInput
from app.integrations.google_calendar import CALENDAR_READONLY_SCOPE, CALENDAR_SCOPE
from app.integrations.google_common import RetryPolicy
from app.services.approvals import generate_approval_token
from app.services.calendar import CalendarService
from app.services.google_auth import GoogleApiClient
from app.tools import GoogleCalendarTools, build_calendar_tool_registry


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

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("DELETE", url, **kwargs)


async def _no_sleep(_: float) -> None:
    return None


def _tools(
    transport: FakeGoogleTransport,
    *,
    retry_policy: RetryPolicy | None = None,
) -> GoogleCalendarTools:
    client = GoogleApiClient(
        _access_token="test-access-token",
        base_url="https://google.test",
        transport=transport,
    )
    return GoogleCalendarTools(
        CalendarService.from_client(
            client,
            retry_policy=retry_policy,
            sleep=_no_sleep if retry_policy is not None else None,
        )
    )


def _event_payload() -> dict[str, Any]:
    return {
        "id": "event-1",
        "etag": '"event-etag-1"',
        "summary": "Planning",
        "start": {"dateTime": "2026-08-20T09:00:00+07:00", "timeZone": "Asia/Ho_Chi_Minh"},
        "end": {"dateTime": "2026-08-20T10:00:00+07:00", "timeZone": "Asia/Ho_Chi_Minh"},
        "attendees": [],
    }


def test_calendar_registry_contains_all_p7_tools_and_classifies_mutations() -> None:
    registry = build_calendar_tool_registry()
    definitions = {definition.name: definition for definition in registry.list()}

    assert len(definitions) == 10
    assert definitions["calendar.list_events"].action_class == ActionClass.READ
    assert definitions["calendar.find_free_slots"].action_class == ActionClass.READ
    assert definitions["calendar.create_event"].action_class == ActionClass.EXTERNAL_COMMUNICATION
    assert definitions["calendar.update_event"].risk_level == ActionRiskLevel.HIGH_IMPACT_WRITE
    assert definitions["calendar.delete_event"].action_class == ActionClass.DESTRUCTIVE
    assert definitions["calendar.delete_event"].risk_level == ActionRiskLevel.IRREVERSIBLE
    assert all(definition.required_scopes for definition in definitions.values())
    assert CALENDAR_READONLY_SCOPE != CALENDAR_SCOPE
    for name in (
        "calendar.list_events",
        "calendar.search_events",
        "calendar.get_event",
        "calendar.get_free_busy",
        "calendar.find_free_slots",
    ):
        assert definitions[name].required_scopes == [CALENDAR_READONLY_SCOPE]
    for name in (
        "calendar.create_event",
        "calendar.update_event",
        "calendar.delete_event",
        "calendar.add_attendee",
        "calendar.remove_attendee",
    ):
        assert definitions[name].required_scopes == [CALENDAR_SCOPE]
    for name in (
        "calendar.update_event",
        "calendar.delete_event",
        "calendar.add_attendee",
        "calendar.remove_attendee",
    ):
        assert "expected_etag" in definitions[name].parameters_schema["properties"]
    assert (
        "expected_etag" not in definitions["calendar.create_event"].parameters_schema["properties"]
    )


@pytest.mark.asyncio
async def test_read_only_calendar_view_cannot_execute_create_or_delete() -> None:
    transport = FakeGoogleTransport([])
    tools = _tools(transport)
    context = ToolContext(run_id="run-1", user_id="user-1", read_only_view=True)

    create_result = await tools.execute(
        ToolInput(
            tool_name="calendar.create_event",
            arguments={
                "start": "2026-08-20T09:00:00+07:00",
                "end": "2026-08-20T10:00:00+07:00",
                "time_zone": "Asia/Ho_Chi_Minh",
            },
        ),
        context,
    )
    delete_result = await tools.execute(
        ToolInput(tool_name="calendar.delete_event", arguments={"event_id": "event-1"}),
        context,
    )

    assert create_result.success is False
    assert delete_result.success is False
    assert "Read-only" in (create_result.error or "")
    assert "Read-only" in (delete_result.error or "")
    assert transport.calls == []


@pytest.mark.asyncio
async def test_calendar_tool_dispatches_free_busy_and_normalizes_output() -> None:
    transport = FakeGoogleTransport(
        [
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
        ]
    )
    tools = _tools(transport)

    result = await tools.execute(
        ToolInput(
            tool_name="calendar.get_free_busy",
            arguments={
                "time_min": "2026-08-20T09:00:00+07:00",
                "time_max": "2026-08-20T12:00:00+07:00",
                "time_zone": "Asia/Ho_Chi_Minh",
            },
        ),
        ToolContext(run_id="run-1", user_id="user-1"),
    )

    assert result.success is True
    free_busy = result.output
    assert free_busy is not None
    assert free_busy.calendars["primary"].busy[0].start == datetime(2026, 8, 20, 2, 30, tzinfo=UTC)
    assert result.metadata.retry_count == 0


@pytest.mark.asyncio
async def test_calendar_tool_reports_retries_across_attendee_read_modify_write() -> None:
    transport = FakeGoogleTransport(
        [
            httpx.Response(503, json={"error": "temporary"}),
            httpx.Response(200, json=_event_payload()),
            httpx.Response(200, json=_event_payload()),
            httpx.Response(
                200,
                json={**_event_payload(), "attendees": [{"email": "new@example.com"}]},
            ),
        ]
    )
    tools = _tools(
        transport,
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0, max_delay_seconds=0),
    )

    args = {
        "event_id": "event-1",
        "email": "new@example.com",
        "send_updates": "none",
        "expected_etag": '"event-etag-1"',
    }
    token = generate_approval_token(
        approval_id="cal-test-appr",
        tool_name="calendar.add_attendee",
        run_id="run-1",
        arguments=args,
    )
    result = await tools.execute(
        ToolInput(tool_name="calendar.add_attendee", arguments=args),
        ToolContext(run_id="run-1", user_id="user-1", approval_token=token),
    )

    assert result.success is True
    assert result.metadata.retry_count == 1
    assert [call[0] for call in transport.calls] == ["GET", "GET", "GET", "PATCH"]


@pytest.mark.asyncio
async def test_mutation_tool_fails_without_approval_token() -> None:
    transport = FakeGoogleTransport([])
    tools = _tools(transport)
    context = ToolContext(run_id="run-1", user_id="user-1")

    result = await tools.execute(
        ToolInput(
            tool_name="calendar.create_event",
            arguments={
                "start": "2026-08-20T09:00:00+07:00",
                "end": "2026-08-20T10:00:00+07:00",
                "time_zone": "Asia/Ho_Chi_Minh",
            },
        ),
        context,
    )
    assert result.success is False
    assert "requires human approval verification" in (result.error or "")


@pytest.mark.asyncio
async def test_calendar_mutation_fails_with_spoofed_or_expired_token() -> None:
    transport = FakeGoogleTransport([])
    tools = _tools(transport)
    # 1. Arbitrary spoofed token fails closed (H1)
    context_spoofed = ToolContext(run_id="run-1", user_id="user-1", approval_token="test-approved")
    res_spoofed = await tools.execute(
        ToolInput(
            tool_name="calendar.create_event",
            arguments={
                "start": "2026-08-20T09:00:00+07:00",
                "end": "2026-08-20T10:00:00+07:00",
                "time_zone": "Asia/Ho_Chi_Minh",
            },
        ),
        context_spoofed,
    )
    assert res_spoofed.success is False
    assert "rejected: approval token is invalid, expired, or unverified" in (
        res_spoofed.error or ""
    )

    # 2. Token issued for different tool fails closed
    wrong_token = generate_approval_token(
        approval_id="cal-req-2", tool_name="calendar.delete_event"
    )
    context_wrong = ToolContext(run_id="run-1", user_id="user-1", approval_token=wrong_token)
    res_wrong = await tools.execute(
        ToolInput(
            tool_name="calendar.create_event",
            arguments={
                "start": "2026-08-20T09:00:00+07:00",
                "end": "2026-08-20T10:00:00+07:00",
                "time_zone": "Asia/Ho_Chi_Minh",
            },
        ),
        context_wrong,
    )
    assert res_wrong.success is False
    assert "rejected: approval token is invalid, expired, or unverified" in (res_wrong.error or "")


@pytest.mark.asyncio
async def test_calendar_update_rejects_stale_live_etag() -> None:
    """When expected_etag is set, live GET ETag is curr_fp and a mismatch fails closed (M3)."""
    payload = _event_payload()
    payload["etag"] = '"event-etag-live"'
    transport = FakeGoogleTransport([httpx.Response(200, json=payload)])
    tools = _tools(transport)
    args = {
        "event_id": "event-1",
        "summary": "Updated",
        "expected_etag": '"event-etag-stale"',
    }
    token = generate_approval_token(
        approval_id="cal-stale-etag",
        tool_name="calendar.update_event",
        run_id="run-1",
        arguments=args,
    )
    result = await tools.execute(
        ToolInput(tool_name="calendar.update_event", arguments=args),
        ToolContext(run_id="run-1", user_id="user-1", approval_token=token),
    )
    assert result.success is False
    assert "stale target state" in (result.error or "")
    assert [call[0] for call in transport.calls] == ["GET"]


@pytest.mark.asyncio
async def test_calendar_mutation_rejects_approval_id_uuid() -> None:
    transport = FakeGoogleTransport([])
    tools = _tools(transport)
    result = await tools.execute(
        ToolInput(
            tool_name="calendar.create_event",
            arguments={
                "start": "2026-08-20T09:00:00+07:00",
                "end": "2026-08-20T10:00:00+07:00",
                "time_zone": "Asia/Ho_Chi_Minh",
                "approval_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            },
        ),
        ToolContext(run_id="run-1", user_id="user-1"),
    )
    assert result.success is False
    assert "approval_id is a request UUID" in (result.error or "")
