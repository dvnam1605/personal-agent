"""Fakes and builders shared by the proactive worker tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.google.calendar import (
    CalendarAttendee,
    CalendarEvent,
    CalendarEventPage,
    CalendarEventTime,
)
from app.domain.models.google.communication import GmailMessagePage, GmailMessageSummary

ZONE = "Asia/Ho_Chi_Minh"


def make_event(
    event_id: str,
    start: datetime,
    *,
    summary: str = "Họp dự án",
    minutes: int = 60,
    attendees: list[str] | None = None,
    status: str = "confirmed",
) -> CalendarEvent:
    """Timed event in the local zone; ``attendees`` are other people's emails."""
    people = [CalendarAttendee(email="me@example.com", self_attendee=True)]
    people.extend(CalendarAttendee(email=email) for email in attendees or [])
    return CalendarEvent(
        id=event_id,
        summary=summary,
        status=status,
        start=CalendarEventTime(value=start, time_zone=ZONE),
        end=CalendarEventTime(value=start + timedelta(minutes=minutes), time_zone=ZONE),
        attendees=people,
    )


def local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(ZONE))


class FakeCalendar:
    """Returns a fixed page and records the windows it was asked for."""

    def __init__(self, events: list[CalendarEvent] | None = None, error: Exception | None = None):
        self.events = events or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def list_events(self, calendar_id: str, **kwargs: Any) -> CalendarEventPage:
        self.calls.append({"calendar_id": calendar_id, **kwargs})
        if self.error is not None:
            raise self.error
        return CalendarEventPage(items=self.events)


class FakeMail:
    """Search returns summaries; ``get_message`` is absent so snippets are used as-is."""

    def __init__(self, snippets: list[str] | None = None, error: Exception | None = None):
        self.snippets = snippets or []
        self.error = error
        self.queries: list[str] = []

    async def search_messages(self, query: str, page_size: int = 3) -> GmailMessagePage:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return GmailMessagePage(
            items=[
                GmailMessageSummary(id=f"m{index}", thread_id=f"t{index}", snippet=snippet)
                for index, snippet in enumerate(self.snippets[:page_size])
            ]
        )


class FakeSources:
    """ProactiveSources over the fakes above; ``retrieved`` records RAG topics."""

    def __init__(
        self,
        calendar: FakeCalendar | None = None,
        mail: FakeMail | None = None,
        retrieve_result: Any = None,
        retrieve_error: Exception | None = None,
    ) -> None:
        self.calendar_service = calendar or FakeCalendar()
        self.mail_service = mail or FakeMail()
        self.retrieve_result = retrieve_result
        self.retrieve_error = retrieve_error
        self.retrieved: list[str] = []

    async def calendar(self, session: AsyncSession, user_id: str) -> FakeCalendar:
        return self.calendar_service

    async def communication(self, session: AsyncSession, user_id: str) -> FakeMail:
        return self.mail_service

    async def retrieve(self, topic: str, user_id: str) -> Any:
        self.retrieved.append(topic)
        if self.retrieve_error is not None:
            raise self.retrieve_error
        return self.retrieve_result
