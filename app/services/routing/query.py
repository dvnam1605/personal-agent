"""Natural-language query orchestration for the HTTP `/query` path.

FastTriage still chooses the route. Execution here uses live Calendar / Gmail /
retrieval services instead of the unwired default harness dispatcher.
Mutations never hit Google directly: they become approval requests.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.declarations import (
    CALENDAR_AGENT_NAME,
    COMMUNICATION_AGENT_NAME,
    KNOWLEDGE_RESEARCH_AGENT_NAME,
)
from app.agents.specialist.calendar import DEFAULT_TIMEZONE, build_event_proposal
from app.domain.enums import RouteType, RunStatus, is_supervisor_route, is_workflow_route
from app.domain.errors import (
    AppError,
    AuthenticationError,
    ExternalServiceError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.models.routing.route import RouteDecision
from app.infrastructure.db.models import User
from app.services.approvals import ApprovalRequestService
from app.services.platform.run_persistence import RunPersistenceService
from app.services.routing.triage import FastTriage
from app.services.skills.matching import unaccent_vietnamese

GOOGLE_CONNECT_HINT = (
    "Google chưa được kết nối hoặc token không còn hiệu lực. Mở GET /auth/google/start rồi thử lại."
)

logger = logging.getLogger(__name__)

_CALENDAR_CREATE = re.compile(
    r"\b(tao|dat|book|create|schedule|them\s+lich|dat\s+lich|xep\s+lich)\b",
    re.IGNORECASE,
)
_CALENDAR_DELETE = re.compile(r"\b(xoa|huy|delete|cancel)\b", re.IGNORECASE)
_CALENDAR_UPDATE = re.compile(r"\b(sua|update)\b", re.IGNORECASE)
_COMM_MUTATION = re.compile(
    r"\b("
    r"gui\s+(email|mail|thu|tin)|"
    r"soan\s+(email|mail|thu|tin)|"
    r"xoa\s+(email|mail|thu)|"
    r"archive|trash|forward|"
    r"send\s+(email|mail)|"
    r"tra\s+loi|"
    r"phan\s+hoi|"
    r"chuyen\s+tiep"
    r")\b",
    re.IGNORECASE,
)
_GMAIL_UNREAD = re.compile(r"\b(chua\s+doc|unread|is:unread)\b", re.IGNORECASE)
_GMAIL_LATEST = re.compile(r"\b(moi\s+nhat|gan\s+day|latest|recent)\b", re.IGNORECASE)
_GMAIL_FROM = re.compile(
    r"\b(?:tu|from)\s+(?:anh|chi|ong|ba)?\s*"
    r"([a-z0-9][a-z0-9._%+\-]*(?:@[a-z0-9.\-]+\.[a-z]{2,})?)\b",
    re.IGNORECASE,
)
_GMAIL_CUA = re.compile(
    r"\bcua\s+(?:anh|chi|ong|ba)?\s*([a-z0-9][a-z0-9._\-]+)\b",
    re.IGNORECASE,
)
_GMAIL_SELF = frozenset({"toi", "minh", "ta", "ban", "tui"})
_DAY_TOMORROW = re.compile(
    r"\b(ngay\s+mai|sang\s+mai|chieu\s+mai|toi\s+mai)\b",
    re.IGNORECASE,
)
_DAY_TODAY = re.compile(r"\b(hom\s+nay|today)\b", re.IGNORECASE)
_DAY_THIS_WEEK = re.compile(r"\b(tuan\s+nay|this\s+week)\b", re.IGNORECASE)
_DAY_MAI = re.compile(r"\bmai\b", re.IGNORECASE)
_WEEKDAY_RE = re.compile(
    r"\b(?:ngay\s+)?(chu\s+nhat|cn|thu\s+[2-7]|t[2-7]|thu\s+(?:hai|ba|tu|nam|sau|bay))\b",
    re.IGNORECASE,
)
_WEEKDAY_INDEX = {
    "thu hai": 0,
    "thu 2": 0,
    "t2": 0,
    "thu ba": 1,
    "thu 3": 1,
    "t3": 1,
    "thu tu": 2,
    "thu 4": 2,
    "t4": 2,
    "thu nam": 3,
    "thu 5": 3,
    "t5": 3,
    "thu sau": 4,
    "thu 6": 4,
    "t6": 4,
    "thu bay": 5,
    "thu 7": 5,
    "t7": 5,
    "chu nhat": 6,
    "cn": 6,
}
_TIME_RE = re.compile(
    r"\b(\d{1,2})\s*(?:h|gio|:)\s*(\d{1,2})?\s*(sang|chieu|toi|trua|am|pm)?\b",
    re.IGNORECASE,
)
_DEFAULT_EVENT_DURATION = timedelta(hours=1)


class QueryRouteInfo(BaseModel):
    """Triage metadata returned with every `/query` response."""

    model_config = ConfigDict(extra="forbid")

    route_type: str
    target_agent: str | None = None
    target_workflow_id: str | None = None
    reason_code: str | None = None
    domains: list[str] = Field(default_factory=list)
    confidence: float


class QueryResult(BaseModel):
    """Stable JSON body for `POST /query`."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    message: str
    route: QueryRouteInfo
    data: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None


CalendarFactory = Callable[[AsyncSession, str], Awaitable[Any]]
CommunicationFactory = Callable[[AsyncSession, str], Awaitable[Any]]
RetrieveFn = Callable[[str, str], Awaitable[Any]]
ClockFn = Callable[[], datetime]
RunIdFn = Callable[[], str]
SummarizeEmailsFn = Callable[[str, list[dict[str, Any]]], Awaitable[str]]


def route_info(decision: RouteDecision) -> QueryRouteInfo:
    """Project a RouteDecision into the HTTP response shape."""
    return QueryRouteInfo(
        route_type=decision.route_type.value,
        target_agent=decision.target_agent,
        target_workflow_id=decision.target_workflow_id,
        reason_code=decision.reason_code,
        domains=[domain.value for domain in decision.domains],
        confidence=decision.confidence,
    )


def infer_calendar_window(
    query: str,
    *,
    now: datetime,
    timezone: str = DEFAULT_TIMEZONE,
) -> tuple[datetime, datetime]:
    """Map relative Vietnamese time phrases onto a half-open local window."""
    zone = ZoneInfo(timezone)
    local_now = _as_local(now, zone)
    today = local_now.date()
    unaccented = unaccent_vietnamese(query)

    if _DAY_TOMORROW.search(unaccented) or (
        _DAY_MAI.search(unaccented) and not _DAY_TODAY.search(unaccented)
    ):
        start = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=zone)
        return start, start + timedelta(days=1)
    if _DAY_TODAY.search(unaccented):
        start = datetime.combine(today, datetime.min.time(), tzinfo=zone)
        return start, start + timedelta(days=1)
    if _DAY_THIS_WEEK.search(unaccented):
        monday = today - timedelta(days=today.weekday())
        start = datetime.combine(monday, datetime.min.time(), tzinfo=zone)
        return start, start + timedelta(days=7)
    return local_now, local_now + timedelta(days=7)


def infer_past_calendar_window(
    query: str,
    *,
    now: datetime,
    timezone: str = DEFAULT_TIMEZONE,
) -> tuple[datetime, datetime]:
    """Window for follow-up: hôm nay, hôm qua, or the previous 14 days."""
    zone = ZoneInfo(timezone)
    local_now = _as_local(now, zone)
    today = local_now.date()
    unaccented = unaccent_vietnamese(query)
    if re.search(r"\bhom\s+qua\b", unaccented, re.IGNORECASE):
        start = datetime.combine(today - timedelta(days=1), datetime.min.time(), tzinfo=zone)
        return start, datetime.combine(today, datetime.min.time(), tzinfo=zone)
    if _DAY_TODAY.search(unaccented):
        start = datetime.combine(today, datetime.min.time(), tzinfo=zone)
        return start, local_now
    return local_now - timedelta(days=14), local_now


def calendar_mutation_kind(query: str) -> str | None:
    """Return create/update/delete when the unaccented text is a calendar write."""
    unaccented = unaccent_vietnamese(query)
    if _CALENDAR_DELETE.search(unaccented):
        return "delete_event"
    if _CALENDAR_UPDATE.search(unaccented):
        return "update_event"
    if _CALENDAR_CREATE.search(unaccented):
        return "create_event"
    return None


def is_communication_mutation(query: str) -> bool:
    """True when the query looks like a Gmail write rather than a read."""
    return _COMM_MUTATION.search(unaccent_vietnamese(query)) is not None


def build_gmail_search_query(query: str) -> tuple[str, int]:
    """Map a Vietnamese inbox request onto a Gmail search string and page size."""
    unaccented = unaccent_vietnamese(query)
    parts = ["in:inbox"]
    page_size = 5 if _GMAIL_LATEST.search(unaccented) else 8
    if _GMAIL_UNREAD.search(unaccented):
        parts.append("is:unread")
    if _DAY_TODAY.search(unaccented):
        parts.append("newer_than:1d")
    from_match = _GMAIL_FROM.search(unaccented) or _GMAIL_CUA.search(unaccented)
    if from_match:
        token = from_match.group(1).strip(".,;:!?")
        if token.casefold() not in _GMAIL_SELF:
            parts.append(f"from:{token}")
    return " ".join(parts), page_size


def parse_event_times(
    query: str,
    *,
    now: datetime,
    timezone: str = DEFAULT_TIMEZONE,
    duration: timedelta = _DEFAULT_EVENT_DURATION,
) -> tuple[datetime, datetime] | None:
    """Parse `10h sáng mai` style phrases into a local start/end window."""
    zone = ZoneInfo(timezone)
    local_now = _as_local(now, zone)
    unaccented = unaccent_vietnamese(query)
    match = _TIME_RE.search(unaccented)
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    period = (match.group(3) or "").casefold()
    if minute > 59:
        return None
    hour = _apply_period(hour, period)
    if hour > 23:
        return None

    weekday_match = _WEEKDAY_RE.search(unaccented)
    if weekday_match:
        norm = re.sub(r"\s+", " ", weekday_match.group(1).casefold())
        target_wd = _WEEKDAY_INDEX.get(norm)
        if target_wd is not None:
            days_ahead = (target_wd - local_now.date().weekday()) % 7
            if days_ahead == 0:
                candidate = datetime.combine(
                    local_now.date(), datetime.min.time(), tzinfo=zone
                ).replace(hour=hour, minute=minute)
                if candidate <= local_now:
                    days_ahead = 7
            day = local_now.date() + timedelta(days=days_ahead)
        else:
            day = local_now.date() + timedelta(days=1)
    elif _DAY_TOMORROW.search(unaccented) or (
        _DAY_MAI.search(unaccented) and not _DAY_TODAY.search(unaccented)
    ):
        day = local_now.date() + timedelta(days=1)
    elif _DAY_TODAY.search(unaccented):
        day = local_now.date()
    else:
        candidate = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=zone).replace(
            hour=hour, minute=minute
        )
        day = local_now.date() if candidate > local_now else local_now.date() + timedelta(days=1)

    start = datetime.combine(day, datetime.min.time(), tzinfo=zone).replace(
        hour=hour, minute=minute
    )
    return start, start + duration


def infer_event_summary(query: str) -> str:
    """Pick a short event title from a Vietnamese create-event phrase."""
    explicit_match = re.search(
        r"(?:với\s+|voi\s+)?(?:lịch\s+là|lich\s+la|tiêu\s+đề\s+(?:là\s+)?|tieu\s+de\s+(?:la\s+)?|nội\s+dung\s+(?:là\s+)?|noi\s+dung\s+(?:la\s+)?|tên\s+(?:là\s+)?|ten\s+(?:la\s+)?)\s*[:=]?\s*(.+)$",
        query,
        re.IGNORECASE,
    )
    if explicit_match:
        extracted = explicit_match.group(1).strip(" .,;:!?\"'")
        extracted = re.sub(
            r"\s+(?:vào\s+lúc|vao\s+luc|vào|vao|lúc|luc)\s+\d+.*$",
            "",
            extracted,
            flags=re.IGNORECASE,
        ).strip(" .,;:!?\"'")
        if extracted:
            return extracted[:80].strip().capitalize()

    unaccented = unaccent_vietnamese(query)
    cleaned = _TIME_RE.sub(" ", unaccented)
    cleaned = _DAY_TOMORROW.sub(" ", cleaned)
    cleaned = _DAY_TODAY.sub(" ", cleaned)
    cleaned = _DAY_MAI.sub(" ", cleaned)
    cleaned = _WEEKDAY_RE.sub(" ", cleaned)
    cleaned = re.sub(
        r"\b(sang|chieu|toi|trua|am|pm|buoi\s+sang|buoi\s+chieu|buoi\s+toi)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(tao|dat|book|create|schedule|lich|luc|vao|cho toi|giup|toi|mot|cuoc)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned or cleaned.casefold() == "hop":
        return "Họp"
    return cleaned[:80].strip().capitalize()


def _apply_period(hour: int, period: str) -> int:
    if period in {"chieu", "toi", "pm"} and hour < 12:
        return hour + 12
    if period == "trua" and hour < 12:
        return 12
    if period == "am" and hour == 12:
        return 0
    return hour


def _as_local(now: datetime, zone: ZoneInfo) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(zone)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def retrieval_requester_id(user_id: str) -> str | None:
    """Retrieval SQL only accepts UUID owners; ``default-user`` sees shared docs."""
    try:
        return str(uuid.UUID(user_id))
    except ValueError:
        return None


class QueryOrchestrator:
    """Triage a natural-language query and execute the matching live specialist."""

    def __init__(
        self,
        *,
        triage: FastTriage | None = None,
        calendar_for_user: CalendarFactory | None = None,
        communication_for_user: CommunicationFactory | None = None,
        retrieve: RetrieveFn | None = None,
        oauth_service: Any | None = None,
        clock: ClockFn | None = None,
        new_run_id: RunIdFn | None = None,
        summarize_emails_fn: SummarizeEmailsFn | None = None,
    ) -> None:
        self._triage = triage or FastTriage()
        self._calendar_for_user = calendar_for_user
        self._communication_for_user = communication_for_user
        self._retrieve = retrieve
        self._oauth_service = oauth_service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_run_id = new_run_id or (lambda: str(uuid.uuid4()))
        self._summarize_emails_fn = summarize_emails_fn
        self._pipeline: Any | None = None

    async def handle(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        *,
        correlation_id: str | None = None,
    ) -> QueryResult:
        """Persist a run, execute the routed specialist, and return a QueryResult."""
        started = time.perf_counter()
        decision = self._triage.triage(query)
        await self._ensure_user(session, user_id)
        run_id = self._new_run_id()
        await RunPersistenceService.create_run(
            session,
            run_id=run_id,
            user_id=user_id,
            request=query,
            route_type=decision.route_type.value,
            domains=[domain.value for domain in decision.domains],
            complexity=decision.complexity.value,
            correlation_id=correlation_id or run_id,
            workflow_name=decision.target_workflow_id,
            active_skill=decision.target_agent,
            goal=query,
        )

        try:
            result = await self._dispatch(session, user_id, query, run_id, decision)
        except (
            AuthenticationError,
            ExternalServiceError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._complete(session, run_id, started, status=RunStatus.COMPLETED)
            return QueryResult(
                run_id=run_id,
                status="blocked",
                message=_blocked_message(exc),
                route=route_info(decision),
                data={"error_code": exc.code},
            )
        except Exception:  # noqa: BLE001 - persist FAILED then let FastAPI's 500 handler run
            await self._complete(
                session,
                run_id,
                started,
                status=RunStatus.FAILED,
                error_summary="Query execution failed.",
            )
            raise

        if result.status != "needs_approval":
            await self._complete(session, run_id, started, status=RunStatus.COMPLETED)
        return result

    async def _dispatch(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        decision: RouteDecision,
    ) -> QueryResult:
        route = route_info(decision)
        if decision.route_type is RouteType.REJECT:
            return QueryResult(
                run_id=run_id,
                status="rejected",
                message="Yêu cầu bị từ chối vì vi phạm ranh giới an toàn.",
                route=route,
            )
        if decision.route_type is RouteType.CLARIFICATION:
            return QueryResult(
                run_id=run_id,
                status="clarification_needed",
                message=(
                    "Bạn có thể nói rõ hơn được không? Ví dụ: xem lịch ngày mai, "
                    "hỏi tài liệu nội bộ, hoặc tạo một cuộc họp."
                ),
                route=route,
            )
        if decision.route_type is RouteType.CASUAL_RESPONSE:
            return QueryResult(
                run_id=run_id,
                status="casual_response",
                message="Xin chào. Bạn cần tôi giúp gì?",
                route=route,
            )
        if is_workflow_route(decision.route_type) or is_supervisor_route(decision.route_type):
            handled = await self._handle_workflow(session, user_id, query, run_id, decision, route)
            if handled is not None:
                return handled
            target = decision.target_workflow_id or decision.route_type.value
            return QueryResult(
                run_id=run_id,
                status="routed",
                message=(
                    f"Yêu cầu được định tuyến tới {target}, nhưng POST /query "
                    "chưa thực thi workflow/supervisor trên đường HTTP này."
                ),
                route=route,
            )
        if decision.route_type is not RouteType.DIRECT_SPECIALIST:
            return QueryResult(
                run_id=run_id,
                status="routed",
                message="Yêu cầu đã được định tuyến nhưng POST /query chưa thực thi đường này.",
                route=route,
            )

        agent = decision.target_agent
        if agent == CALENDAR_AGENT_NAME:
            return await self._handle_calendar(session, user_id, query, run_id, route)
        if agent == KNOWLEDGE_RESEARCH_AGENT_NAME:
            return await self._handle_knowledge(user_id, query, run_id, route)
        if agent == COMMUNICATION_AGENT_NAME:
            return await self._handle_communication(session, user_id, query, run_id, route)
        return QueryResult(
            run_id=run_id,
            status="routed",
            message=(
                f"Đã định tuyến tới {agent or 'specialist'}, nhưng POST /query "
                "chưa thực thi agent này."
            ),
            route=route,
        )

    async def _handle_workflow(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        decision: RouteDecision,
        route: QueryRouteInfo,
    ) -> QueryResult | None:
        from app.services.routing.live_workflows import run_meeting_followup, run_meeting_prep

        skill = ""
        if isinstance(decision.parameters, dict):
            skill = str(decision.parameters.get("skill") or "")
        workflow_id = decision.target_workflow_id or ""
        now = self._clock()

        async def list_events(window_start: datetime, window_end: datetime) -> Any:
            calendar = await self._calendar(session, user_id)
            return await calendar.list_events(
                "primary",
                time_min=window_start,
                time_max=window_end,
                page_size=20,
                time_zone=DEFAULT_TIMEZONE,
            )

        async def search_messages(mail_query: str, page_size: int = 3) -> Any:
            service = await self._communication(session, user_id)
            return await service.search_messages(mail_query, page_size=page_size)

        if workflow_id == "WF-05" or skill == "meeting-prep":

            async def retrieve(topic: str) -> Any:
                return await self._run_retrieval(topic, user_id)

            return await run_meeting_prep(
                session=session,
                user_id=user_id,
                query=query,
                run_id=run_id,
                route=route,
                now=now,
                list_events=list_events,
                search_messages=search_messages,
                retrieve=retrieve,
            )
        if workflow_id == "WF-01" or skill == "email-follow-up":
            return await run_meeting_followup(
                session=session,
                query=query,
                run_id=run_id,
                route=route,
                now=now,
                list_events=list_events,
                search_messages=search_messages,
            )
        return None

    async def _handle_calendar(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        route: QueryRouteInfo,
    ) -> QueryResult:
        kind = calendar_mutation_kind(query)
        if kind in {"delete_event", "update_event"}:
            return QueryResult(
                run_id=run_id,
                status="clarification_needed",
                message=(
                    "Để sửa hoặc xóa lịch, cho biết event_id (hoặc duyệt một đề xuất "
                    "từ luồng approvals). POST /query chỉ tạo đề xuất khi đủ thời gian."
                ),
                route=route,
            )
        if kind == "create_event":
            return await self._propose_create_event(session, query, run_id, route)

        now = self._clock()
        window_start, window_end = infer_calendar_window(query, now=now)
        calendar = await self._calendar(session, user_id)
        page = await calendar.list_events(
            "primary",
            time_min=window_start,
            time_max=window_end,
            page_size=20,
            time_zone=DEFAULT_TIMEZONE,
        )
        events = [_serialize_event(event) for event in list(getattr(page, "items", []) or [])]
        if not events:
            message = "Không có sự kiện nào trong khoảng thời gian này."
        else:
            lines = [f"- {item['when']}: {item['summary']}" for item in events]
            message = "Lịch của bạn:\n" + "\n".join(lines)
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=message,
            route=route,
            data={
                "window": {"start": _iso(window_start), "end": _iso(window_end)},
                "events": events,
                "count": len(events),
            },
        )

    async def _propose_create_event(
        self,
        session: AsyncSession,
        query: str,
        run_id: str,
        route: QueryRouteInfo,
    ) -> QueryResult:
        parsed = parse_event_times(query, now=self._clock())
        if parsed is None:
            return QueryResult(
                run_id=run_id,
                status="clarification_needed",
                message=(
                    "Để tạo lịch, cho biết thời gian bắt đầu (ví dụ: 10h sáng mai) "
                    "và tiêu đề cuộc họp."
                ),
                route=route,
            )
        start, end = parsed
        summary = infer_event_summary(query)
        try:
            proposal = build_event_proposal(
                summary=summary,
                start=_iso(start),
                end=_iso(end),
                timezone=DEFAULT_TIMEZONE,
                description=query,
            )
        except ValidationError as exc:
            return QueryResult(
                run_id=run_id,
                status="clarification_needed",
                message=exc.message,
                route=route,
            )
        approval = await ApprovalRequestService.create_request(session, run_id, proposal)
        return QueryResult(
            run_id=run_id,
            status="needs_approval",
            message=(
                f"{proposal.description} Vui lòng xác nhận bên dưới để tạo sự kiện trên Google Calendar."
            ),
            route=route,
            data={
                "proposal": proposal.model_dump(mode="json"),
                "start": _iso(start),
                "end": _iso(end),
            },
            approval_id=approval.id,
        )

    async def _handle_knowledge(
        self,
        user_id: str,
        query: str,
        run_id: str,
        route: QueryRouteInfo,
    ) -> QueryResult:
        synthesis = await self._run_retrieval(query, user_id)
        citations = [
            citation.model_dump(mode="json")
            for citation in list(getattr(synthesis, "citations", []))
        ]
        status = getattr(getattr(synthesis, "status", None), "value", None)
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=str(getattr(synthesis, "answer", "")),
            route=route,
            data={"citations": citations, "sufficiency": status},
        )

    async def _handle_communication(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        route: QueryRouteInfo,
    ) -> QueryResult:
        if is_communication_mutation(query):
            return QueryResult(
                run_id=run_id,
                status="routed",
                message=(
                    "Gửi hoặc xóa email không được thực hiện trực tiếp trên POST /query. "
                    "Dùng luồng duyệt (approvals) sau khi soạn thảo."
                ),
                route=route,
            )
        gmail_query, page_size = build_gmail_search_query(query)
        service = await self._communication(session, user_id)
        page = await service.search_messages(gmail_query, page_size=page_size)
        summaries = list(getattr(page, "items", []) or [])
        messages = await _load_gmail_details(service, summaries)
        if not messages:
            message = "Không có email nào khớp trong hộp thư đến."
        else:
            if self._summarize_emails_fn is not None:
                message = await self._summarize_emails_fn(query, messages)
            else:
                message = await summarize_emails(query, messages)
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=message,
            route=route,
            data={"query": gmail_query, "messages": messages, "count": len(messages)},
        )

    async def _calendar(self, session: AsyncSession, user_id: str) -> Any:
        if self._calendar_for_user is not None:
            return await self._calendar_for_user(session, user_id)
        from app.api.routes.google_auth import google_oauth_service
        from app.services.google.calendar import CalendarService

        oauth = self._oauth_service or google_oauth_service
        return await CalendarService.for_user(oauth, session, user_id)

    async def _communication(self, session: AsyncSession, user_id: str) -> Any:
        if self._communication_for_user is not None:
            return await self._communication_for_user(session, user_id)
        from app.api.routes.google_auth import google_oauth_service
        from app.integrations.google_gmail import GMAIL_MODIFY_SCOPE
        from app.services.google.communication import CommunicationService

        oauth = self._oauth_service or google_oauth_service
        client = await oauth.create_client(
            session,
            user_id,
            required_scopes=[GMAIL_MODIFY_SCOPE],
        )
        return CommunicationService.from_client(client)

    async def _run_retrieval(self, query: str, user_id: str) -> Any:
        if self._retrieve is not None:
            return await self._retrieve(query, user_id)
        if self._pipeline is None:
            from app.services.retrieval.factory import build_retrieval_pipeline

            self._pipeline = build_retrieval_pipeline(use_viranker=False)
        from app.domain.models.retrieval import RetrievalQuery

        retrieval_query = RetrievalQuery(
            original_query=query,
            search_query=query,
            requester_id=retrieval_requester_id(user_id),
        )
        return await self._pipeline.run_with_synthesis(retrieval_query, internal_only=True)

    @staticmethod
    async def _ensure_user(session: AsyncSession, user_id: str) -> None:
        existing = await session.get(User, user_id)
        if existing is not None:
            return
        collision = await session.scalar(
            select(User.id).where(User.email == f"{user_id}@users.local")
        )
        if collision is None:
            session.add(User(id=user_id, email=f"{user_id}@users.local"))
            await session.flush()
            return
        session.add(User(id=user_id, email=f"{user_id}-{uuid.uuid4().hex[:8]}@users.local"))
        await session.flush()

    @staticmethod
    async def _complete(
        session: AsyncSession,
        run_id: str,
        started: float,
        *,
        status: RunStatus,
        error_summary: str | None = None,
    ) -> None:
        latency_ms = (time.perf_counter() - started) * 1000.0
        await RunPersistenceService.complete_run(
            session,
            run_id,
            status,
            latency_ms,
            error_summary=error_summary,
        )


def _blocked_message(exc: AppError) -> str:
    details = exc.details or {}
    if details.get("missing_scopes"):
        return (
            "Google chưa cấp quyền Gmail. Mở GET /auth/google/start để cấp lại scope, rồi thử lại."
        )
    text = (exc.message or "").casefold()
    if "google" in text or exc.code in {"UNAUTHENTICATED", "EXTERNAL_SERVICE_ERROR"}:
        return GOOGLE_CONNECT_HINT
    return "Không thực hiện được yêu cầu vì dịch vụ bên ngoài chưa sẵn sàng."


async def _load_gmail_details(service: Any, summaries: list[Any]) -> list[dict[str, Any]]:
    get_message = getattr(service, "get_message", None)
    if get_message is None:
        return [_serialize_message(item) for item in summaries]

    async def _one(summary: Any) -> Any:
        identifier = getattr(summary, "id", "")
        if not identifier:
            return summary
        try:
            return await get_message(identifier, format="metadata")
        except AppError:
            return summary

    details = await asyncio.gather(*[_one(item) for item in summaries])
    return [_serialize_message(item) for item in details]


def _serialize_message(message: Any) -> dict[str, Any]:
    sender = getattr(message, "sender", None) or getattr(message, "from_address", None)
    display = getattr(sender, "display_name", None) or "" if sender is not None else ""
    email = getattr(sender, "email", "") if sender is not None else ""
    from_label = f"{display} <{email}>".strip() if display and email else (display or email)
    internal_date = getattr(message, "internal_date", None)
    labels = [str(item) for item in list(getattr(message, "label_ids", []) or [])]
    subject = getattr(message, "subject", None) or "(không tiêu đề)"
    return {
        "id": getattr(message, "id", ""),
        "thread_id": getattr(message, "thread_id", ""),
        "subject": subject,
        "from": from_label,
        "from_email": email or None,
        "when": _iso(internal_date) if isinstance(internal_date, datetime) else "",
        "snippet": getattr(message, "snippet", "") or "",
        "unread": "UNREAD" in {label.upper() for label in labels},
    }


def _serialize_event(event: Any) -> dict[str, Any]:
    start = event.start.to_google() if hasattr(event.start, "to_google") else {}
    end = event.end.to_google() if hasattr(event.end, "to_google") else {}
    when = start.get("dateTime") or start.get("date") or ""
    summary = getattr(event, "summary", "") or "(không tiêu đề)"
    return {
        "id": getattr(event, "id", ""),
        "summary": summary,
        "when": when,
        "start": start,
        "end": end,
        "location": getattr(event, "location", None),
        "html_link": getattr(event, "html_link", None),
    }


def fallback_summarize_emails(query: str, messages: list[dict[str, Any]]) -> str:
    """Deterministic, structured summary when LLM is offline or times out."""
    total = len(messages)
    unread_count = sum(1 for m in messages if m.get("unread"))

    critical: list[dict[str, Any]] = []
    work: list[dict[str, Any]] = []
    news: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []

    _WORK_KEYWORDS = (
        "linkedin",
        "job",
        "career",
        "recruitment",
        "tuyen dung",
        "ung tuyen",
        "phong van",
        "interview",
        "hr",
        "offer",
        "hiring",
        "developer",
        "engineer",
    )
    _CRITICAL_KEYWORDS = (
        "canh bao",
        "bao mat",
        "security",
        "otp",
        "xac minh",
        "xac thuc",
        "ma dung mot lan",
        "mat ma",
        "ngan hang",
        "bank",
        "vpbank",
        "vietcombank",
        "techcombank",
        "giao dich",
        "transfer",
        "thanh toan",
        "payment",
        "invoice",
        "hoa don",
    )
    _NEWS_KEYWORDS = (
        "medium",
        "digest",
        "newsletter",
        "youtube",
        "dang ky",
        "hoi vien",
        "khuyen mai",
        "quang cao",
        "promo",
        "daily",
    )

    for m in messages:
        text = unaccent_vietnamese(
            f"{m.get('from', '')} {m.get('subject', '')} {m.get('snippet', '')}"
        ).casefold()

        # Check work first so "jobalerts" doesn't collide with security "alert"
        if any(k in text for k in _WORK_KEYWORDS):
            work.append(m)
        elif any(k in text for k in _CRITICAL_KEYWORDS):
            critical.append(m)
        elif any(k in text for k in _NEWS_KEYWORDS):
            news.append(m)
        else:
            others.append(m)

    sections = []
    unread_note = f" ({unread_count} email chưa đọc)" if unread_count else ""
    sections.append(f"📌 **Tổng quan**: Tìm thấy {total} email trong hộp thư{unread_note}.")

    def _fmt_item(m: dict[str, Any]) -> str:
        sender = m.get("from") or m.get("from_email") or "Không rõ người gửi"
        subject = m.get("subject") or "(Không có tiêu đề)"
        snippet = m.get("snippet", "").strip()
        snippet_text = f' — *"{snippet[:120]}..."*' if snippet else ""
        return f"- **{subject}** ({sender}){snippet_text}"

    if critical:
        sections.append(
            "🔴 **Quan trọng / Cần lưu ý ngay**:\n" + "\n".join(_fmt_item(m) for m in critical)
        )
    if work:
        sections.append("💼 **Công việc & Tuyển dụng**:\n" + "\n".join(_fmt_item(m) for m in work))
    if news:
        sections.append(
            "📰 **Bản tin & Thông báo dịch vụ**:\n" + "\n".join(_fmt_item(m) for m in news)
        )
    if others:
        sections.append("✉️ **Email khác**:\n" + "\n".join(_fmt_item(m) for m in others))

    return "\n\n".join(sections)


async def summarize_emails(
    query: str,
    messages: list[dict[str, Any]],
    *,
    timeout_seconds: float = 12.0,
) -> str:
    """Summarize inbox messages using LLM when available, falling back to heuristic categorization."""
    if not messages:
        return "Không có email nào khớp trong hộp thư đến."

    try:
        from app.core.config import settings

        api_key = settings.llm.openai_api_key
        if api_key:
            import httpx

            system_prompt = (
                "Bạn là Namm Agent - trợ lý điều hành AI chuyên nghiệp.\n"
                "Nhiệm vụ: Đọc nội dung email rồi viết BÁO CÁO TÓM TẮT tự nhiên bằng tiếng Việt.\n\n"
                "Quy tắc bắt buộc:\n"
                "- KHÔNG dùng heading markdown (# ## ###). Chỉ dùng emoji + **in đậm** làm đề mục.\n"
                "- KHÔNG lặp lại nguyên văn tiêu đề email; phải đọc snippet để tóm tắt giá trị thực.\n"
                "- Viết tự nhiên như đang nói chuyện với chủ nhân hộp thư, ngắn gọn mà đủ ý.\n\n"
                "Cấu trúc phản hồi (đúng thứ tự):\n\n"
                "📌 **Tổng quan nhanh**\n"
                "1-2 câu tóm gọn: có bao nhiêu email, điểm đáng chú ý nhất là gì.\n\n"
                "🔴 **Quan trọng / Cần lưu ý ngay**\n"
                "Liệt kê dạng bullet, mỗi item viết rõ hành động hoặc thông tin cốt lõi "
                "(bảo mật tài khoản, mã OTP, giao dịch ngân hàng...).\n\n"
                "💼 **Công việc & Tuyển dụng**\n"
                "Cơ hội việc làm, thông tin đồng nghiệp/đối tác (nếu có).\n\n"
                "📰 **Bản tin & Khác**\n"
                "Bài viết, thông báo dịch vụ, quà tặng... (nếu có).\n\n"
                "Bỏ qua mục nào không có email phù hợp. Không thêm lời kết thừa."
            )

            email_entries = []
            for i, item in enumerate(messages, 1):
                sender = item.get("from") or item.get("from_email") or "Không rõ người gửi"
                subject = item.get("subject") or "(Không có tiêu đề)"
                when = item.get("when") or ""
                status = "Chưa đọc" if item.get("unread") else "Đã đọc"
                snippet = item.get("snippet") or ""
                email_entries.append(
                    f"{i}. Từ: {sender}\n"
                    f"   Tiêu đề: {subject}\n"
                    f"   Thời gian: {when} ({status})\n"
                    f"   Trích đoạn nội dung: {snippet}"
                )

            user_content = (
                f"Yêu cầu của người dùng: {query}\n\n"
                f"Danh sách {len(messages)} email nhận được:\n\n" + "\n\n".join(email_entries)
            )

            model = settings.llm.fast_model
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                resp = await client.post(
                    settings.llm.chat_completions_url(),
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "temperature": 0.2,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = str(data["choices"][0]["message"]["content"]).strip()
                    if content:
                        return content
    except Exception:  # noqa: BLE001 - LLM is optional; fall back to heuristic summary
        logger.exception("llm_email_summarization_failed")

    return fallback_summarize_emails(query, messages)


__all__ = [
    "GOOGLE_CONNECT_HINT",
    "QueryOrchestrator",
    "QueryResult",
    "QueryRouteInfo",
    "build_gmail_search_query",
    "calendar_mutation_kind",
    "fallback_summarize_emails",
    "infer_calendar_window",
    "infer_event_summary",
    "infer_past_calendar_window",
    "is_communication_mutation",
    "parse_event_times",
    "retrieval_requester_id",
    "route_info",
    "summarize_emails",
]
