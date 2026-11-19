"""Meeting minutes on request ("biên bản cuộc họp hôm nay")."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.specialist.calendar import DEFAULT_TIMEZONE
from app.domain.enums import RouteType
from app.domain.models.routing.route import RouteDecision
from app.services.meetings.minutes import find_transcript, write_minutes
from app.services.skills.matching import unaccent_vietnamese

from .goals import GoalMixin
from .models import QueryResult, route_info

# A time word is required: "viết biên bản họp … từ tài liệu" is document generation.
_MINUTES = re.compile(
    r"\b(?:bien ban|tom tat) (?:cuoc )?hop\b.*?"
    r"\b(?:hom nay|hom qua|vua roi|vua xong|gan nhat|moi nhat|luc nay)\b"
)
_YESTERDAY = re.compile(r"\bhom qua\b")


class MinutesMixin(GoalMixin):
    """Write minutes for the latest ended meeting of the day from its Meet transcript.

    The transcript is found in Drive (read-only scope) and reaches the model only inside
    an untrusted boundary; nothing is shared or sent.
    """

    async def _drive(self, session: AsyncSession, user_id: str) -> Any:
        if self._drive_for_user is not None:
            return await self._drive_for_user(session, user_id)
        from app.api.routes.google_auth import google_oauth_service
        from app.services.google.drive import DriveService

        oauth = self._oauth_service or google_oauth_service
        return await DriveService.for_user(oauth, session, user_id)

    async def _maybe_handle_minutes(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        decision: RouteDecision,
    ) -> QueryResult | None:
        """Answer "biên bản/tóm tắt cuộc họp [hôm qua]"; ``None`` for other turns."""
        folded = unaccent_vietnamese(query)
        if decision.route_type is RouteType.REJECT or not _MINUTES.search(folded):
            return None
        route = route_info(decision)
        zone = ZoneInfo(DEFAULT_TIMEZONE)
        now = self._clock()
        day = now.astimezone(zone).date() - timedelta(days=1 if _YESTERDAY.search(folded) else 0)
        start = datetime.combine(day, datetime.min.time(), tzinfo=zone)
        calendar = await self._calendar(session, user_id)
        page = await calendar.list_events(
            "primary", time_min=start, time_max=min(now, start + timedelta(days=1)), page_size=20
        )
        meetings = [
            event
            for event in list(getattr(page, "items", []) or [])
            if not getattr(event.start, "all_day", False) and event.end.as_datetime() <= now
        ]
        if not meetings:
            return QueryResult(
                run_id=run_id,
                status="completed",
                message="Không có cuộc họp nào đã kết thúc trong ngày đó.",
                route=route,
            )
        meeting = max(meetings, key=lambda event: event.end.as_datetime())
        title = getattr(meeting, "summary", "") or ""
        found = await find_transcript(
            await self._drive(session, user_id), title, meeting.start.as_datetime()
        )
        if found is None:
            return QueryResult(
                run_id=run_id,
                status="completed",
                message=(
                    f"Chưa thấy bản ghi (transcript) của “{title}” trong Drive. Bật ghi lời thoại "
                    "trong Google Meet; bản ghi thường có sau khi họp 10–30 phút."
                ),
                route=route,
            )
        transcript_file, text = found
        when = f"{meeting.start.as_datetime().astimezone(zone):%H:%M %d/%m/%Y}"
        attendees = [a.email for a in meeting.attendees if not a.self_attendee]
        minutes = await write_minutes(title, when, attendees, transcript_file, text)
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=minutes.markdown(),
            route=route,
            data={"meeting_id": meeting.id, "action_items": minutes.action_items},
        )
