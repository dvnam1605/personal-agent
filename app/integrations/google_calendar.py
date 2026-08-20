"""Deterministic Google Calendar v3 adapter.

Provider payloads are normalized at this boundary.  The service layer never
needs to reason about Google field names, RFC3339 formatting, or pagination.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models import (
    CalendarAttendee,
    CalendarBusyCalendar,
    CalendarBusyInterval,
    CalendarEvent,
    CalendarEventPage,
    CalendarEventRequest,
    CalendarEventTime,
    CalendarEventUpdate,
    CalendarFreeBusy,
    CalendarMutationResult,
    normalize_aware_datetime,
    parse_calendar_value,
    validate_timezone_name,
)
from app.integrations.google_common import (
    GoogleResourceAdapter,
    require_list,
    require_object,
)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_MAX_PAGE_SIZE = 2500
CALENDAR_MAX_FREE_BUSY_CALENDARS = 50
CALENDAR_SEND_UPDATES = ("all", "externalOnly", "none")


def _validate_identifier(value: str, label: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or "?" in normalized
        or "#" in normalized
        or ".." in normalized
        or "\\" in normalized
    ):
        raise DomainValidationError(f"Invalid Google Calendar {label}.")
    return normalized


def _validate_page_size(value: int) -> int:
    if not 1 <= value <= CALENDAR_MAX_PAGE_SIZE:
        raise DomainValidationError(
            f"Calendar page size must be between 1 and {CALENDAR_MAX_PAGE_SIZE}.",
            details={"page_size": value},
        )
    return value


def _validate_send_updates(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if normalized not in CALENDAR_SEND_UPDATES:
        raise DomainValidationError(
            "Calendar send_updates must be one of all, externalOnly, or none.",
            details={"send_updates": value},
        )
    return normalized


def _normalize_window(
    time_min: datetime,
    time_max: datetime,
    *,
    time_zone: str | None = None,
) -> tuple[datetime, datetime, str | None]:
    zone_name = validate_timezone_name(time_zone)
    start = normalize_aware_datetime(time_min, time_zone=zone_name)
    end = normalize_aware_datetime(time_max, time_zone=zone_name)
    if end <= start:
        raise DomainValidationError("Calendar time_max must be after time_min.")
    return start, end, zone_name


def _normalize_optional_bounds(
    time_min: datetime | None,
    time_max: datetime | None,
    *,
    time_zone: str | None,
) -> tuple[datetime | None, datetime | None]:
    start = normalize_aware_datetime(time_min, time_zone=time_zone) if time_min is not None else None
    end = normalize_aware_datetime(time_max, time_zone=time_zone) if time_max is not None else None
    if start is not None and end is not None and end <= start:
        raise DomainValidationError("Calendar time_max must be after time_min.")
    return start, end


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _event_from_payload(payload: object, calendar_id: str) -> CalendarEvent:
    data = require_object(payload, "Google Calendar event normalization")
    event_id = data.get("id")
    raw_start = data.get("start")
    raw_end = data.get("end")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ExternalServiceError(
            "Google returned a Calendar event without an identifier.", service_name="calendar"
        )
    try:
        attendees: list[CalendarAttendee] = []
        raw_attendees = data.get("attendees", [])
        if isinstance(raw_attendees, list):
            for raw_attendee in raw_attendees:
                attendee = CalendarAttendee.from_google(raw_attendee)
                if attendee is not None:
                    attendees.append(attendee)

        def optional_datetime(field_name: str) -> datetime | None:
            value = data.get(field_name)
            if not isinstance(value, str) or not value.strip():
                return None
            return datetime.fromisoformat(value.replace("Z", "+00:00"))

        return CalendarEvent(
            id=event_id,
            calendar_id=calendar_id,
            etag=data.get("etag") if isinstance(data.get("etag"), str) else None,
            status=str(data.get("status") or "confirmed"),
            summary=str(data.get("summary") or ""),
            description=data.get("description")
            if isinstance(data.get("description"), str)
            else None,
            location=data.get("location") if isinstance(data.get("location"), str) else None,
            start=CalendarEventTime.from_google(raw_start),
            end=CalendarEventTime.from_google(raw_end),
            attendees=attendees,
            html_link=data.get("htmlLink") if isinstance(data.get("htmlLink"), str) else None,
            i_cal_uid=data.get("iCalUID") if isinstance(data.get("iCalUID"), str) else None,
            recurring_event_id=data.get("recurringEventId")
            if isinstance(data.get("recurringEventId"), str)
            else None,
            created_at=optional_datetime("created"),
            updated_at=optional_datetime("updated"),
        )
    except (PydanticValidationError, TypeError, ValueError) as exc:
        raise ExternalServiceError(
            "Google returned an invalid Calendar event.", service_name="calendar"
        ) from exc


def _result_size_estimate(data: dict[str, Any]) -> int | None:
    value = data.get("resultSizeEstimate", data.get("totalItems"))
    return value if isinstance(value, int) and value >= 0 else None


def _calendar_ids(value: Iterable[str] | str) -> list[str]:
    values = [value] if isinstance(value, str) else list(value)
    result: list[str] = []
    for item in values:
        normalized = _validate_identifier(item, "calendar identifier")
        if normalized not in result:
            result.append(normalized)
    if not result:
        raise DomainValidationError("At least one Calendar identifier is required.")
    if len(result) > CALENDAR_MAX_FREE_BUSY_CALENDARS:
        raise DomainValidationError(
            f"Calendar free/busy supports at most {CALENDAR_MAX_FREE_BUSY_CALENDARS} calendars.",
            details={"calendar_count": len(result)},
        )
    return result


class CalendarAdapter(GoogleResourceAdapter):
    """Typed Google Calendar adapter with normalized errors and bounded retries."""

    required_scope = CALENDAR_SCOPE

    async def list_events(
        self,
        calendar_id: str = "primary",
        *,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        page_size: int = 100,
        page_token: str | None = None,
        time_zone: str | None = None,
        show_deleted: bool = False,
    ) -> CalendarEventPage:
        """List expanded events in stable start-time order."""
        identifier = _validate_identifier(calendar_id, "calendar identifier")
        zone_name = validate_timezone_name(time_zone)
        params: dict[str, Any] = {
            "maxResults": _validate_page_size(page_size),
            "singleEvents": True,
            "orderBy": "startTime",
            "showDeleted": show_deleted,
        }
        start, end = _normalize_optional_bounds(time_min, time_max, time_zone=zone_name)
        if start is not None:
            params["timeMin"] = _rfc3339(start)
        if end is not None:
            params["timeMax"] = _rfc3339(end)
        if zone_name:
            params["timeZone"] = zone_name
        if page_token and page_token.strip():
            params["pageToken"] = page_token.strip()

        payload = await self._request_json(
            "GET",
            f"/calendar/v3/calendars/{quote(identifier, safe='')}/events",
            operation="Google Calendar event listing",
            params=params,
        )
        data = require_object(payload, "Google Calendar event listing")
        raw_items = require_list(data, "items", "Google Calendar event listing")
        events = [_event_from_payload(item, identifier) for item in raw_items]
        return CalendarEventPage(
            items=events,
            next_page_token=data.get("nextPageToken")
            if isinstance(data.get("nextPageToken"), str)
            else None,
            result_size_estimate=_result_size_estimate(data),
        )

    async def search_events(
        self,
        query: str,
        calendar_id: str = "primary",
        *,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        page_size: int = 100,
        page_token: str | None = None,
        time_zone: str | None = None,
        show_deleted: bool = False,
    ) -> CalendarEventPage:
        """Search event text through the provider's deterministic q parameter."""
        normalized_query = query.strip()
        if not normalized_query:
            raise DomainValidationError("A non-blank Calendar search query is required.")
        identifier = _validate_identifier(calendar_id, "calendar identifier")
        # Keep one wire implementation so listing/search have identical boundary behavior.
        zone_name = validate_timezone_name(time_zone)
        params: dict[str, Any] = {
            "q": normalized_query,
            "maxResults": _validate_page_size(page_size),
            "singleEvents": True,
            "orderBy": "startTime",
            "showDeleted": show_deleted,
        }
        start, end = _normalize_optional_bounds(time_min, time_max, time_zone=zone_name)
        if start is not None:
            params["timeMin"] = _rfc3339(start)
        if end is not None:
            params["timeMax"] = _rfc3339(end)
        if zone_name:
            params["timeZone"] = zone_name
        if page_token and page_token.strip():
            params["pageToken"] = page_token.strip()
        payload = await self._request_json(
            "GET",
            f"/calendar/v3/calendars/{quote(identifier, safe='')}/events",
            operation="Google Calendar event search",
            params=params,
        )
        data = require_object(payload, "Google Calendar event search")
        raw_items = require_list(data, "items", "Google Calendar event search")
        return CalendarEventPage(
            items=[_event_from_payload(item, identifier) for item in raw_items],
            next_page_token=data.get("nextPageToken")
            if isinstance(data.get("nextPageToken"), str)
            else None,
            result_size_estimate=_result_size_estimate(data),
        )

    async def get_event(self, event_id: str, calendar_id: str = "primary") -> CalendarEvent:
        """Fetch one normalized Calendar event."""
        calendar_identifier = _validate_identifier(calendar_id, "calendar identifier")
        event_identifier = _validate_identifier(event_id, "event identifier")
        payload = await self._request_json(
            "GET",
            f"/calendar/v3/calendars/{quote(calendar_identifier, safe='')}/events/{quote(event_identifier, safe='')}",
            operation="Google Calendar event lookup",
        )
        return _event_from_payload(payload, calendar_identifier)

    async def get_free_busy(
        self,
        time_min: datetime,
        time_max: datetime,
        calendar_ids: Iterable[str] | str = ("primary",),
        *,
        time_zone: str | None = None,
    ) -> CalendarFreeBusy:
        """Fetch normalized busy intervals for one or more calendars."""
        start, end, zone_name = _normalize_window(time_min, time_max, time_zone=time_zone)
        identifiers = _calendar_ids(calendar_ids)
        body: dict[str, Any] = {
            "timeMin": _rfc3339(start),
            "timeMax": _rfc3339(end),
            "items": [{"id": identifier} for identifier in identifiers],
        }
        if zone_name:
            body["timeZone"] = zone_name
        payload = await self._request_json(
            "POST",
            "/calendar/v3/freeBusy",
            operation="Google Calendar free/busy lookup",
            json=body,
            retryable=True,
        )
        data = require_object(payload, "Google Calendar free/busy lookup")
        raw_calendars = data.get("calendars", {})
        if not isinstance(raw_calendars, dict):
            raise ExternalServiceError(
                "Google returned an invalid Calendar free/busy response.", service_name="calendar"
            )
        calendars: dict[str, CalendarBusyCalendar] = {}
        for identifier in identifiers:
            if identifier not in raw_calendars:
                calendars[identifier] = CalendarBusyCalendar(
                    calendar_id=identifier,
                    errors=["Calendar was not returned by Google free/busy response."],
                )
                continue
            calendars[identifier] = self._busy_calendar_from_payload(
                identifier, raw_calendars[identifier]
            )
        return CalendarFreeBusy(time_min=start, time_max=end, calendars=calendars)

    async def create_event(
        self,
        request: CalendarEventRequest,
        calendar_id: str = "primary",
        *,
        send_updates: str = "all",
    ) -> CalendarEvent:
        """Create an event; creation is intentionally never retried automatically."""
        identifier = _validate_identifier(calendar_id, "calendar identifier")
        payload = await self._request_json(
            "POST",
            f"/calendar/v3/calendars/{quote(identifier, safe='')}/events",
            operation="Google Calendar event creation",
            params={"sendUpdates": _validate_send_updates(send_updates)},
            json=request.to_google(),
            retryable=False,
        )
        return _event_from_payload(payload, identifier)

    async def update_event(
        self,
        event_id: str,
        request: CalendarEventRequest | CalendarEventUpdate,
        calendar_id: str = "primary",
        *,
        send_updates: str = "all",
    ) -> CalendarEvent:
        """Update an event with PUT for full requests or PATCH for partial requests."""
        calendar_identifier = _validate_identifier(calendar_id, "calendar identifier")
        event_identifier = _validate_identifier(event_id, "event identifier")
        method = "PUT" if isinstance(request, CalendarEventRequest) else "PATCH"
        payload = await self._request_json(
            method,
            f"/calendar/v3/calendars/{quote(calendar_identifier, safe='')}/events/{quote(event_identifier, safe='')}",
            operation="Google Calendar event update",
            params={"sendUpdates": _validate_send_updates(send_updates)},
            json=request.to_google(),
            retryable=False,
        )
        return _event_from_payload(payload, calendar_identifier)

    async def delete_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
        *,
        send_updates: str = "all",
    ) -> CalendarMutationResult:
        """Delete an event and return a provider-neutral mutation result."""
        calendar_identifier = _validate_identifier(calendar_id, "calendar identifier")
        event_identifier = _validate_identifier(event_id, "event identifier")
        await self._request_json(
            "DELETE",
            f"/calendar/v3/calendars/{quote(calendar_identifier, safe='')}/events/{quote(event_identifier, safe='')}",
            operation="Google Calendar event deletion",
            params={"sendUpdates": _validate_send_updates(send_updates)},
            allow_empty=True,
            retryable=False,
        )
        return CalendarMutationResult(
            resource_id=event_identifier,
            operation="delete_event",
            deleted=True,
        )

    async def add_attendee(
        self,
        event_id: str,
        attendee: CalendarAttendee,
        calendar_id: str = "primary",
        *,
        send_updates: str = "all",
    ) -> CalendarEvent:
        """Add one attendee without duplicating an existing mailbox."""
        current = await self.get_event(event_id, calendar_id)
        if any(item.email == attendee.email for item in current.attendees):
            return current
        return await self._patch_attendees(
            current,
            [*current.attendees, attendee],
            send_updates=send_updates,
        )

    async def remove_attendee(
        self,
        event_id: str,
        email: str,
        calendar_id: str = "primary",
        *,
        send_updates: str = "all",
    ) -> CalendarEvent:
        """Remove one attendee by normalized email without guessing."""
        current = await self.get_event(event_id, calendar_id)
        normalized = CalendarAttendee(email=email).email
        remaining = [item for item in current.attendees if item.email != normalized]
        if len(remaining) == len(current.attendees):
            return current
        return await self._patch_attendees(
            current,
            remaining,
            send_updates=send_updates,
        )

    async def _patch_attendees(
        self,
        current: CalendarEvent,
        attendees: list[CalendarAttendee],
        *,
        send_updates: str,
    ) -> CalendarEvent:
        if not current.etag:
            raise ExternalServiceError(
                "Google Calendar event did not include an ETag; attendee update was aborted.",
                service_name="calendar",
                details={"calendar_id": current.calendar_id, "event_id": current.id},
            )
        payload = await self._request_json(
            "PATCH",
            f"/calendar/v3/calendars/{quote(current.calendar_id, safe='')}/events/{quote(current.id, safe='')}",
            operation="Google Calendar attendee update",
            params={"sendUpdates": _validate_send_updates(send_updates)},
            json={"attendees": [attendee.to_google() for attendee in attendees]},
            headers={"If-Match": current.etag},
            retryable=False,
        )
        return _event_from_payload(payload, current.calendar_id)

    @staticmethod
    def _busy_calendar_from_payload(calendar_id: str, payload: object) -> CalendarBusyCalendar:
        if not isinstance(payload, dict):
            return CalendarBusyCalendar(
                calendar_id=calendar_id,
                errors=["Google returned an invalid free/busy calendar entry."],
            )
        data = payload
        raw_busy = data.get("busy", [])
        if not isinstance(raw_busy, list):
            raise ExternalServiceError(
                "Google returned invalid busy intervals.", service_name="calendar"
            )
        busy: list[CalendarBusyInterval] = []
        try:
            for item in raw_busy:
                if not isinstance(item, dict):
                    raise ValueError("Busy interval must be an object.")
                raw_start = item.get("start")
                raw_end = item.get("end")
                if not isinstance(raw_start, str) or not isinstance(raw_end, str):
                    raise ValueError("Busy intervals must include datetime start and end.")
                parsed_start = parse_calendar_value(raw_start)
                parsed_end = parse_calendar_value(raw_end)
                if not isinstance(parsed_start, datetime) or not isinstance(parsed_end, datetime):
                    raise ValueError("Busy intervals must use datetime values.")
                busy.append(
                    CalendarBusyInterval(
                        start=parsed_start,
                        end=parsed_end,
                    )
                )
        except (PydanticValidationError, TypeError, ValueError) as exc:
            raise ExternalServiceError(
                "Google returned invalid busy intervals.", service_name="calendar"
            ) from exc
        errors: list[str] = []
        raw_errors = data.get("errors", [])
        if isinstance(raw_errors, list):
            for item in raw_errors:
                if not isinstance(item, dict):
                    errors.append("Google returned an invalid free/busy calendar error.")
                    continue
                reason = item.get("reason")
                message = item.get("message")
                if isinstance(reason, str) and isinstance(message, str):
                    errors.append(f"{reason}: {message}")
                elif isinstance(message, str):
                    errors.append(message)
                elif isinstance(reason, str):
                    errors.append(reason)
                else:
                    errors.append("Google returned an invalid free/busy calendar error.")
        elif raw_errors is not None:
            errors.append("Google returned invalid free/busy calendar errors.")
        return CalendarBusyCalendar(calendar_id=calendar_id, busy=busy, errors=errors)


GoogleCalendarAdapter = CalendarAdapter


__all__ = [
    "CALENDAR_MAX_PAGE_SIZE",
    "CALENDAR_MAX_FREE_BUSY_CALENDARS",
    "CALENDAR_READONLY_SCOPE",
    "CALENDAR_SCOPE",
    "CALENDAR_SEND_UPDATES",
    "CalendarAdapter",
    "GoogleCalendarAdapter",
]
