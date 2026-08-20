"""Provider-neutral, timezone-aware Calendar contracts.

The Google Calendar adapter owns provider wire formats.  These models are the
stable boundary used by services, tools, and future calendar agents.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CalendarValue = datetime | date


def parse_calendar_value(value: object) -> CalendarValue:
    """Parse an ISO date or RFC3339 datetime without silently dropping timezone data."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError("Calendar date/time cannot be blank.")
        try:
            if "T" in normalized or " " in normalized:
                return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            return date.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("Calendar date/time must be ISO formatted.") from exc
    raise TypeError("Calendar date/time must be a date, datetime, or ISO string.")


def validate_timezone_name(value: str | None) -> str | None:
    """Validate an IANA timezone name while keeping the original canonical input."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {normalized}.") from exc
    return normalized


def normalize_aware_datetime(value: datetime, *, time_zone: str | None = None) -> datetime:
    """Make a datetime timezone-aware and normalize it to UTC."""
    zone = ZoneInfo(time_zone) if time_zone else None
    normalized = value
    if normalized.tzinfo is None or normalized.tzinfo.utcoffset(normalized) is None:
        if zone is None:
            raise ValueError("Timezone-aware datetimes are required for Calendar operations.")
        normalized = normalized.replace(tzinfo=zone)
    return normalized.astimezone(UTC)


class CalendarEventTime(BaseModel):
    """Either a timezone-aware instant or an all-day date range endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: CalendarValue
    time_zone: str | None = None
    all_day: bool = False

    @field_validator("value", mode="before")
    @classmethod
    def parse_value(cls, value: object) -> CalendarValue:
        return parse_calendar_value(value)

    @field_validator("time_zone", mode="after")
    @classmethod
    def validate_time_zone(cls, value: str | None) -> str | None:
        return validate_timezone_name(value)

    @model_validator(mode="after")
    def normalize_value(self) -> CalendarEventTime:
        if self.all_day:
            if isinstance(self.value, datetime):
                zone = ZoneInfo(self.time_zone) if self.time_zone else self.value.tzinfo
                if zone is None:
                    raise ValueError("All-day datetime values require a timezone.")
                object.__setattr__(self, "value", self.value.astimezone(zone).date())
            return self

        if isinstance(self.value, datetime):
            normalized = normalize_aware_datetime(self.value, time_zone=self.time_zone)
        else:
            if not self.time_zone:
                raise ValueError("Timed Calendar values require a timezone when given as a date.")
            normalized = normalize_aware_datetime(
                datetime.combine(self.value, time.min), time_zone=self.time_zone
            )
        object.__setattr__(self, "value", normalized)
        return self

    @property
    def is_all_day(self) -> bool:
        """Whether this endpoint belongs to an all-day event."""
        return self.all_day

    @property
    def date_value(self) -> date | None:
        """Return the normalized date for an all-day endpoint."""
        return self.value if self.all_day and isinstance(self.value, date) else None

    @property
    def datetime_value(self) -> datetime | None:
        """Return the normalized UTC instant for a timed endpoint."""
        return self.value if not self.all_day and isinstance(self.value, datetime) else None

    def as_datetime(self, time_zone: str | None = None) -> datetime:
        """Represent either endpoint as an aware datetime for deterministic arithmetic."""
        if self.datetime_value is not None:
            return self.datetime_value
        if self.date_value is None:
            raise ValueError("Calendar event endpoint has no usable value.")
        zone_name = time_zone or self.time_zone or "UTC"
        zone = ZoneInfo(validate_timezone_name(zone_name) or "UTC")
        return datetime.combine(self.date_value, time.min, tzinfo=zone).astimezone(UTC)

    def to_google(self) -> dict[str, str]:
        """Serialize the endpoint into the Calendar API event-time shape."""
        if self.all_day:
            assert self.date_value is not None
            return {"date": self.date_value.isoformat()}
        assert self.datetime_value is not None
        if self.time_zone:
            local_value = self.datetime_value.astimezone(ZoneInfo(self.time_zone))
            return {"dateTime": local_value.isoformat(), "timeZone": self.time_zone}
        return {"dateTime": self.datetime_value.isoformat().replace("+00:00", "Z")}

    @classmethod
    def from_google(cls, payload: object) -> CalendarEventTime:
        """Normalize a Google event start/end object."""
        if not isinstance(payload, dict):
            raise ValueError("Google Calendar event time must be an object.")
        time_zone = payload.get("timeZone")
        if time_zone is not None and not isinstance(time_zone, str):
            time_zone = None
        raw_date = payload.get("date")
        if isinstance(raw_date, str):
            return cls(
                value=parse_calendar_value(raw_date), time_zone=time_zone, all_day=True
            )
        raw_datetime = payload.get("dateTime")
        if isinstance(raw_datetime, str):
            return cls(
                value=parse_calendar_value(raw_datetime), time_zone=time_zone, all_day=False
            )
        raise ValueError("Google Calendar event time has neither date nor dateTime.")


class CalendarAttendee(BaseModel):
    """Normalized Calendar attendee and response state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    email: str
    display_name: str | None = None
    response_status: str = "needsAction"
    optional: bool = False
    organizer: bool = False
    self_attendee: bool = False

    @field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if (
            not normalized
            or any(char in normalized for char in "\r\n \t")
            or normalized.count("@") != 1
            or normalized.startswith("@")
            or normalized.endswith("@")
        ):
            raise ValueError("A valid Calendar attendee email is required.")
        return normalized

    @field_validator("display_name", mode="after")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("response_status", mode="after")
    @classmethod
    def normalize_response_status(cls, value: str) -> str:
        normalized = value.strip() or "needsAction"
        return normalized

    def to_google(self) -> dict[str, Any]:
        """Serialize only attendee fields accepted on create/update requests."""
        payload: dict[str, Any] = {"email": self.email}
        if self.display_name:
            payload["displayName"] = self.display_name
        if self.optional:
            payload["optional"] = True
        return payload

    @classmethod
    def from_google(cls, payload: object) -> CalendarAttendee | None:
        if not isinstance(payload, dict):
            return None
        email = payload.get("email")
        if not isinstance(email, str) or not email.strip():
            return None
        try:
            return cls(
                email=email,
                display_name=payload.get("displayName")
                if isinstance(payload.get("displayName"), str)
                else None,
                response_status=str(payload.get("responseStatus") or "needsAction"),
                optional=bool(payload.get("optional", False)),
                organizer=bool(payload.get("organizer", False)),
                self_attendee=bool(payload.get("self", False)),
            )
        except ValueError:
            return None


class CalendarEvent(BaseModel):
    """Normalized Calendar event, including both timed and all-day events."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    calendar_id: str = Field(default="primary", min_length=1)
    etag: str | None = None
    status: str = "confirmed"
    summary: str = ""
    description: str | None = None
    location: str | None = None
    start: CalendarEventTime
    end: CalendarEventTime
    attendees: list[CalendarAttendee] = Field(default_factory=list)
    html_link: str | None = None
    i_cal_uid: str | None = None
    recurring_event_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("id", "calendar_id", mode="after")
    @classmethod
    def normalize_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Calendar identifiers cannot be blank.")
        return normalized

    @field_validator("etag", mode="after")
    @classmethod
    def normalize_etag(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return normalize_aware_datetime(value)

    @model_validator(mode="after")
    def validate_range(self) -> CalendarEvent:
        if self.start.all_day != self.end.all_day:
            raise ValueError("Calendar event start and end must both be timed or all-day.")
        if self.start.all_day:
            if not isinstance(self.start.value, date) or not isinstance(self.end.value, date):
                raise ValueError("All-day Calendar events require date endpoints.")
            if self.end.value <= self.start.value:
                raise ValueError("Calendar event end must be after start.")
        elif self.start.as_datetime() >= self.end.as_datetime():
            raise ValueError("Calendar event end must be after start.")
        return self


class CalendarEventPage(BaseModel):
    """Stable paginated Calendar event result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[CalendarEvent] = Field(default_factory=list)
    next_page_token: str | None = None
    result_size_estimate: int | None = Field(default=None, ge=0)

    @field_validator("next_page_token", mode="after")
    @classmethod
    def normalize_page_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class CalendarBusyInterval(BaseModel):
    """A normalized busy interval used by free/busy and slot arithmetic."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime

    @field_validator("start", "end", mode="after")
    @classmethod
    def normalize_timestamps(cls, value: datetime) -> datetime:
        return normalize_aware_datetime(value)

    @model_validator(mode="after")
    def validate_range(self) -> CalendarBusyInterval:
        if self.end <= self.start:
            raise ValueError("Busy interval end must be after start.")
        return self


class CalendarBusyCalendar(BaseModel):
    """Busy intervals and provider-level errors for one calendar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    calendar_id: str = Field(..., min_length=1)
    busy: list[CalendarBusyInterval] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CalendarFreeBusy(BaseModel):
    """Normalized multi-calendar free/busy response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    time_min: datetime
    time_max: datetime
    calendars: dict[str, CalendarBusyCalendar] = Field(default_factory=dict)

    @field_validator("time_min", "time_max", mode="after")
    @classmethod
    def normalize_timestamps(cls, value: datetime) -> datetime:
        return normalize_aware_datetime(value)

    @model_validator(mode="after")
    def validate_range(self) -> CalendarFreeBusy:
        if self.time_max <= self.time_min:
            raise ValueError("Free/busy time_max must be after time_min.")
        return self

    @property
    def busy_intervals(self) -> list[CalendarBusyInterval]:
        """Return all calendar busy intervals in deterministic calendar order."""
        intervals: list[CalendarBusyInterval] = []
        for calendar in self.calendars.values():
            intervals.extend(calendar.busy)
        return sorted(intervals, key=lambda item: (item.start, item.end))


class CalendarSlot(BaseModel):
    """A deterministic candidate meeting slot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime
    duration_minutes: int = Field(..., ge=1)
    time_zone: str = "UTC"

    @field_validator("start", "end", mode="after")
    @classmethod
    def normalize_timestamps(cls, value: datetime) -> datetime:
        return normalize_aware_datetime(value)

    @field_validator("time_zone", mode="after")
    @classmethod
    def validate_time_zone(cls, value: str) -> str:
        return validate_timezone_name(value) or "UTC"

    @model_validator(mode="after")
    def validate_duration(self) -> CalendarSlot:
        actual_minutes = int((self.end - self.start).total_seconds() // 60)
        if self.end <= self.start or actual_minutes != self.duration_minutes:
            raise ValueError("Calendar slot duration does not match its endpoints.")
        return self


class CalendarEventRequest(BaseModel):
    """Typed create-event request independent of Google wire names."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = ""
    start: CalendarEventTime
    end: CalendarEventTime
    description: str | None = None
    location: str | None = None
    attendees: list[CalendarAttendee] = Field(default_factory=list)

    @field_validator("summary", "description", "location", mode="after")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        return normalized.strip() if value != "" else ""

    @model_validator(mode="after")
    def validate_range(self) -> CalendarEventRequest:
        if self.start.all_day != self.end.all_day:
            raise ValueError("Calendar event start and end must both be timed or all-day.")
        if self.start.all_day:
            if self.end.value <= self.start.value:
                raise ValueError("Calendar event end must be after start.")
        elif self.start.as_datetime() >= self.end.as_datetime():
            raise ValueError("Calendar event end must be after start.")
        return self

    def to_google(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "summary": self.summary,
            "start": self.start.to_google(),
            "end": self.end.to_google(),
        }
        if self.description is not None:
            payload["description"] = self.description
        if self.location is not None:
            payload["location"] = self.location
        if self.attendees:
            payload["attendees"] = [attendee.to_google() for attendee in self.attendees]
        return payload

    @classmethod
    def from_values(
        cls,
        *,
        summary: str = "",
        start: CalendarEventTime | CalendarValue | str,
        end: CalendarEventTime | CalendarValue | str,
        time_zone: str | None = None,
        all_day: bool | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: Iterable[CalendarAttendee | str | dict[str, Any]] = (),
    ) -> CalendarEventRequest:
        start_time, end_time = _coerce_event_times(start, end, time_zone=time_zone, all_day=all_day)
        return cls(
            summary=summary,
            start=start_time,
            end=end_time,
            description=description,
            location=location,
            attendees=_coerce_attendees(attendees),
        )


class CalendarEventUpdate(BaseModel):
    """Typed partial update request; omitted fields are left unchanged."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str | None = None
    start: CalendarEventTime | None = None
    end: CalendarEventTime | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[CalendarAttendee] | None = None

    @model_validator(mode="after")
    def validate_update(self) -> CalendarEventUpdate:
        if self.start is not None and self.end is None:
            raise ValueError("Calendar event update requires end when start is provided.")
        if self.end is not None and self.start is None:
            raise ValueError("Calendar event update requires start when end is provided.")
        if self.start is not None and self.end is not None:
            if self.start.all_day != self.end.all_day:
                raise ValueError("Calendar event start and end must both be timed or all-day.")
            if self.start.as_datetime() >= self.end.as_datetime():
                raise ValueError("Calendar event end must be after start.")
        if all(value is None for value in (self.summary, self.start, self.description, self.location, self.attendees)):
            raise ValueError("At least one Calendar event field must be updated.")
        return self

    def to_google(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.summary is not None:
            payload["summary"] = self.summary
        if self.start is not None and self.end is not None:
            payload["start"] = self.start.to_google()
            payload["end"] = self.end.to_google()
        if self.description is not None:
            payload["description"] = self.description
        if self.location is not None:
            payload["location"] = self.location
        if self.attendees is not None:
            payload["attendees"] = [attendee.to_google() for attendee in self.attendees]
        return payload

    @classmethod
    def from_values(
        cls,
        *,
        summary: str | None = None,
        start: CalendarEventTime | CalendarValue | str | None = None,
        end: CalendarEventTime | CalendarValue | str | None = None,
        time_zone: str | None = None,
        all_day: bool | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: Iterable[CalendarAttendee | str | dict[str, Any]] | None = None,
    ) -> CalendarEventUpdate:
        start_time: CalendarEventTime | None = None
        end_time: CalendarEventTime | None = None
        if start is not None or end is not None:
            if start is None or end is None:
                raise ValueError("Calendar event update requires both start and end.")
            start_time, end_time = _coerce_event_times(
                start, end, time_zone=time_zone, all_day=all_day
            )
        return cls(
            summary=summary,
            start=start_time,
            end=end_time,
            description=description,
            location=location,
            attendees=None if attendees is None else _coerce_attendees(attendees),
        )


class CalendarMutationResult(BaseModel):
    """Normalized result for a Calendar mutation without a response body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str = Field(..., min_length=1)
    operation: str = Field(..., min_length=1)
    deleted: bool = False


def _build_event_time(
    value: CalendarEventTime | CalendarValue | str,
    *,
    time_zone: str | None,
    all_day: bool | None,
) -> CalendarEventTime:
    if isinstance(value, CalendarEventTime):
        if all_day is not None and value.all_day != all_day:
            raise ValueError("Calendar event endpoints must agree on all_day.")
        return value
    parsed = parse_calendar_value(value)
    inferred_all_day = isinstance(parsed, date) and not isinstance(parsed, datetime)
    return CalendarEventTime(
        value=parsed,
        time_zone=time_zone,
        all_day=inferred_all_day if all_day is None else all_day,
    )


def _coerce_event_times(
    start: CalendarEventTime | CalendarValue | str,
    end: CalendarEventTime | CalendarValue | str,
    *,
    time_zone: str | None,
    all_day: bool | None,
) -> tuple[CalendarEventTime, CalendarEventTime]:
    start_time = _build_event_time(start, time_zone=time_zone, all_day=all_day)
    end_time = _build_event_time(end, time_zone=time_zone, all_day=all_day)
    if start_time.all_day != end_time.all_day:
        raise ValueError("Calendar event endpoints must agree on all_day.")
    return start_time, end_time


def _coerce_attendees(
    attendees: Iterable[CalendarAttendee | str | dict[str, Any]],
) -> list[CalendarAttendee]:
    result: list[CalendarAttendee] = []
    for attendee in attendees:
        value = attendee if isinstance(attendee, CalendarAttendee) else (
            {"email": attendee} if isinstance(attendee, str) else attendee
        )
        result.append(CalendarAttendee.model_validate(value))
    return result


__all__ = [
    "CalendarAttendee",
    "CalendarBusyCalendar",
    "CalendarBusyInterval",
    "CalendarEvent",
    "CalendarEventPage",
    "CalendarEventRequest",
    "CalendarEventTime",
    "CalendarEventUpdate",
    "CalendarFreeBusy",
    "CalendarMutationResult",
    "CalendarSlot",
    "normalize_aware_datetime",
    "parse_calendar_value",
    "validate_timezone_name",
]
