"""Fakes and builders shared by the proactive worker tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NotFoundError
from app.domain.models.google.calendar import (
    CalendarAttendee,
    CalendarEvent,
    CalendarEventPage,
    CalendarEventTime,
)
from app.domain.models.google.communication import (
    EmailAddress,
    GmailMessage,
    GmailMessagePage,
    GmailMessageSummary,
)

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

    def __init__(
        self,
        snippets: list[str] | None = None,
        error: Exception | None = None,
        messages: list[GmailMessageSummary] | None = None,
    ):
        self.snippets = snippets or []
        self.error = error
        self.messages = messages
        self.queries: list[str] = []

    async def search_messages(self, query: str, page_size: int = 3) -> GmailMessagePage:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        if self.messages is not None:
            return GmailMessagePage(items=self.messages[:page_size])
        return GmailMessagePage(
            items=[
                GmailMessageSummary(id=f"m{index}", thread_id=f"t{index}", snippet=snippet)
                for index, snippet in enumerate(self.snippets[:page_size])
            ]
        )


def make_mail(
    message_id: str, received: datetime, *, subject: str = "Báo giá", sender: str = "nam@x.vn"
) -> GmailMessage:
    """Full Gmail message as ``get_message`` would return it."""
    return GmailMessage(
        id=message_id,
        thread_id=f"t-{message_id}",
        snippet="Gửi anh báo giá mới",
        internal_date=received,
        subject=subject,
        sender=EmailAddress(display_name="Anh Nam", email=sender),
        label_ids=["UNREAD", "IMPORTANT"],
    )


class FakeSources:
    """ProactiveSources over the fakes above; ``retrieved`` records RAG topics."""

    def __init__(
        self,
        calendar: FakeCalendar | None = None,
        mail: FakeMail | None = None,
        retrieve_result: Any = None,
        retrieve_error: Exception | None = None,
        tasks: Any = None,
    ) -> None:
        self.calendar_service = calendar or FakeCalendar()
        self.mail_service = mail or FakeMail()
        self.retrieve_result = retrieve_result
        self.retrieve_error = retrieve_error
        self.retrieved: list[str] = []
        self.tasks_service = tasks

    async def calendar(self, session: AsyncSession, user_id: str) -> FakeCalendar:
        return self.calendar_service

    async def communication(self, session: AsyncSession, user_id: str) -> FakeMail:
        return self.mail_service

    async def retrieve(self, topic: str, user_id: str) -> Any:
        self.retrieved.append(topic)
        if self.retrieve_error is not None:
            raise self.retrieve_error
        return self.retrieve_result

    async def tasks(self, session: AsyncSession, user_id: str) -> Any:
        if self.tasks_service is None:
            raise NotFoundError("Google Tasks is not granted.")
        return self.tasks_service
