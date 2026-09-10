"""Deterministic Calendar domain service and slot arithmetic."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.domain.errors import ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models import (
    CalendarAttendee,
    CalendarBusyInterval,
    CalendarEvent,
    CalendarEventPage,
    CalendarFreeBusy,
    CalendarMutationResult,
    CalendarSlot,
    normalize_aware_datetime,
    validate_timezone_name,
)
from app.integrations.google_calendar import CALENDAR_SCOPE, CalendarAdapter
from app.services.google.auth import AsyncHttpTransport, GoogleApiClient, GoogleOAuthService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _normalize_window(
    window_start: datetime,
    window_end: datetime,
    *,
    time_zone: str | None = None,
) -> tuple[datetime, datetime, str]:
    zone_name = validate_timezone_name(time_zone) or "UTC"
    start = normalize_aware_datetime(window_start, time_zone=time_zone)
    end = normalize_aware_datetime(window_end, time_zone=time_zone)
    if end <= start:
        raise DomainValidationError("Calendar window end must be after start.")
    return start, end, zone_name


def _event_interval(
    event: CalendarEvent,
    *,
    time_zone: str,
) -> CalendarBusyInterval:
    """Convert timed or all-day events into one comparable UTC busy interval."""
    try:
        start = event.start.as_datetime(time_zone)
        end = event.end.as_datetime(time_zone)
        return CalendarBusyInterval(start=start, end=end)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(
            "Calendar event cannot be converted into a busy interval."
        ) from exc


def busy_intervals_from_events(
    events: Iterable[CalendarEvent],
    *,
    time_zone: str = "UTC",
) -> list[CalendarBusyInterval]:
    """Normalize timed and all-day events for deterministic conflict checks."""
    zone_name = validate_timezone_name(time_zone) or "UTC"
    return sorted(
        [_event_interval(event, time_zone=zone_name) for event in events],
        key=lambda interval: (interval.start, interval.end),
    )


def _merge_busy_intervals(
    intervals: Iterable[CalendarBusyInterval],
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[CalendarBusyInterval]:
    clipped: list[CalendarBusyInterval] = []
    for interval in intervals:
        start = max(interval.start, window_start)
        end = min(interval.end, window_end)
        if start < end:
            clipped.append(CalendarBusyInterval(start=start, end=end))
    clipped.sort(key=lambda item: (item.start, item.end))

    merged: list[CalendarBusyInterval] = []
    for interval in clipped:
        if not merged or interval.start > merged[-1].end:
            merged.append(interval)
            continue
        previous = merged[-1]
        merged[-1] = CalendarBusyInterval(
            start=previous.start,
            end=max(previous.end, interval.end),
        )
    return merged


def find_deterministic_free_slots(
    busy_intervals: Iterable[CalendarBusyInterval],
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int,
    *,
    slot_step_minutes: int | None = None,
    max_results: int = 20,
    time_zone: str | None = None,
) -> list[CalendarSlot]:
    """Return reproducible slots using only interval arithmetic.

    Candidates start at ``window_start`` and advance by ``slot_step_minutes``;
    the default step equals the requested duration.  Overlapping or adjacent
    busy intervals are merged before conflict checks, so no LLM or provider
    ordering is involved.
    """
    if duration_minutes < 1 or duration_minutes > 24 * 60:
        raise DomainValidationError("Calendar slot duration must be between 1 and 1440 minutes.")
    step_minutes = duration_minutes if slot_step_minutes is None else slot_step_minutes
    if step_minutes < 1 or step_minutes > 24 * 60:
        raise DomainValidationError("Calendar slot step must be between 1 and 1440 minutes.")
    if max_results < 1:
        raise DomainValidationError("Calendar max_results must be positive.")

    start, end, zone_name = _normalize_window(window_start, window_end, time_zone=time_zone)
    merged = _merge_busy_intervals(
        busy_intervals,
        window_start=start,
        window_end=end,
    )
    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=step_minutes)
    candidate = start
    slots: list[CalendarSlot] = []
    while candidate + duration <= end and len(slots) < max_results:
        candidate_end = candidate + duration
        conflict = any(
            candidate < interval.end and candidate_end > interval.start for interval in merged
        )
        if not conflict:
            slots.append(
                CalendarSlot(
                    start=candidate,
                    end=candidate_end,
                    duration_minutes=duration_minutes,
                    time_zone=zone_name,
                )
            )
        candidate += step
    return slots


class CalendarService:
    """Domain service composing deterministic Calendar adapter operations."""

    def __init__(self, calendar: CalendarAdapter) -> None:
        self.calendar = calendar

    @classmethod
    def from_client(
        cls,
        client: GoogleApiClient,
        *,
        retry_policy=None,
        sleep=None,
    ) -> CalendarService:
        adapter_kwargs = {}
        if retry_policy is not None:
            adapter_kwargs["retry_policy"] = retry_policy
        if sleep is not None:
            adapter_kwargs["sleep"] = sleep
        return cls(CalendarAdapter(client, **adapter_kwargs))

    @classmethod
    async def for_user(
        cls,
        oauth_service: GoogleOAuthService,
        session: AsyncSession,
        user_id: str,
        *,
        required_scopes: Iterable[str] | None = None,
        transport: AsyncHttpTransport | None = None,
        retry_policy=None,
        sleep=None,
    ) -> CalendarService:
        """Refresh credentials if necessary, then build a least-scope client."""
        client = await oauth_service.create_client(
            session,
            user_id,
            required_scopes=(
                list(required_scopes) if required_scopes is not None else [CALENDAR_SCOPE]
            ),
            transport=transport,
        )
        return cls.from_client(client, retry_policy=retry_policy, sleep=sleep)

    async def list_events(self, *args, **kwargs) -> CalendarEventPage:
        return await self.calendar.list_events(*args, **kwargs)

    async def search_events(self, *args, **kwargs) -> CalendarEventPage:
        return await self.calendar.search_events(*args, **kwargs)

    async def get_event(self, *args, **kwargs) -> CalendarEvent:
        return await self.calendar.get_event(*args, **kwargs)

    async def get_free_busy(self, *args, **kwargs) -> CalendarFreeBusy:
        return await self.calendar.get_free_busy(*args, **kwargs)

    async def find_free_slots(
        self,
        window_start: datetime,
        window_end: datetime,
        duration_minutes: int,
        *,
        calendar_ids: Iterable[str] | str = ("primary",),
        slot_step_minutes: int | None = None,
        max_results: int = 20,
        time_zone: str | None = None,
    ) -> list[CalendarSlot]:
        """Fetch busy data and find conflict-free slots deterministically."""
        free_busy = await self.calendar.get_free_busy(
            window_start,
            window_end,
            calendar_ids,
            time_zone=time_zone,
        )
        calendar_errors = {
            calendar_id: list(calendar.errors)
            for calendar_id, calendar in free_busy.calendars.items()
            if calendar.errors
        }
        if calendar_errors:
            raise ExternalServiceError(
                "Google Calendar free/busy data is incomplete; free slots cannot be proposed.",
                service_name="calendar",
                details={"calendar_errors": calendar_errors},
            )
        return find_deterministic_free_slots(
            free_busy.busy_intervals,
            window_start,
            window_end,
            duration_minutes,
            slot_step_minutes=slot_step_minutes,
            max_results=max_results,
            time_zone=time_zone,
        )

    async def create_event(self, *args, **kwargs) -> CalendarEvent:
        return await self.calendar.create_event(*args, **kwargs)

    async def update_event(self, *args, **kwargs) -> CalendarEvent:
        return await self.calendar.update_event(*args, **kwargs)

    async def delete_event(self, *args, **kwargs) -> CalendarMutationResult:
        return await self.calendar.delete_event(*args, **kwargs)

    async def add_attendee(
        self,
        event_id: str,
        attendee: CalendarAttendee | str,
        calendar_id: str = "primary",
        *,
        send_updates: str = "all",
        if_match: str | None = None,
    ) -> CalendarEvent:
        normalized = (
            attendee if isinstance(attendee, CalendarAttendee) else CalendarAttendee(email=attendee)
        )
        return await self.calendar.add_attendee(
            event_id,
            normalized,
            calendar_id,
            send_updates=send_updates,
            if_match=if_match,
        )

    async def remove_attendee(self, *args, **kwargs) -> CalendarEvent:
        return await self.calendar.remove_attendee(*args, **kwargs)


GoogleCalendarService = CalendarService


__all__ = [
    "CalendarService",
    "GoogleCalendarService",
    "busy_intervals_from_events",
    "find_deterministic_free_slots",
]
