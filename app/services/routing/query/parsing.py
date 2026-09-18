"""Query parsing and parameter extraction helpers for the routing orchestrator."""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.agents.specialist.calendar import DEFAULT_TIMEZONE
from app.domain.errors import AppError
from app.services.skills.matching import unaccent_vietnamese

from .models import GOOGLE_CONNECT_HINT

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
_COMM_DRAFT = re.compile(
    r"\b("
    r"soan\s+(email|mail|thu|tin|don)|"
    r"viet\s+(email|mail|thu|don)|"
    r"tao\s+(nhap|ban\s+nhap|draft)|"
    r"gui\s+(email|mail|thu|don)|"
    r"draft\s+(email|mail)"
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


def infer_calendar_window(
    query: str,
    *,
    now: datetime,
    timezone: str = DEFAULT_TIMEZONE,
) -> tuple[datetime, datetime]:
    """Map relative Vietnamese time phrases onto a half-open local window."""
    zone = ZoneInfo(timezone)
    local_now = as_local(now, zone)
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
    local_now = as_local(now, zone)
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


def is_communication_draft(query: str) -> bool:
    """True when the query explicitly asks to compose or draft an email."""
    return _COMM_DRAFT.search(unaccent_vietnamese(query)) is not None


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
    local_now = as_local(now, zone)
    unaccented = unaccent_vietnamese(query)
    match = _TIME_RE.search(unaccented)
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    period = (match.group(3) or "").casefold()
    if minute > 59:
        return None
    hour = apply_period(hour, period)
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


def apply_period(hour: int, period: str) -> int:
    """Apply AM/PM/morning/afternoon offset to hour integer."""
    if period in {"chieu", "toi", "pm"} and hour < 12:
        return hour + 12
    if period == "trua" and hour < 12:
        return 12
    if period == "am" and hour == 12:
        return 0
    return hour


def as_local(now: datetime, zone: ZoneInfo) -> datetime:
    """Convert a datetime to local timezone safely."""
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(zone)


def iso(value: datetime) -> str:
    """Format datetime without microseconds."""
    return value.replace(microsecond=0).isoformat()


def retrieval_requester_id(user_id: str) -> str | None:
    """Retrieval SQL only accepts UUID owners; ``default-user`` sees shared docs."""
    try:
        return str(uuid.UUID(user_id))
    except ValueError:
        return None


def blocked_message(exc: Any) -> str:
    """Return user-friendly error message when external services fail or require re-auth."""
    details = getattr(exc, "details", None) or {}
    if details.get("missing_scopes"):
        return (
            "Google chưa cấp quyền Gmail. Mở GET /auth/google/start để cấp lại scope, rồi thử lại."
        )
    text = (getattr(exc, "message", "") or "").casefold()
    code = getattr(exc, "code", "")
    if "google" in text or code in {"UNAUTHENTICATED", "EXTERNAL_SERVICE_ERROR"}:
        return GOOGLE_CONNECT_HINT
    return "Không thực hiện được yêu cầu vì dịch vụ bên ngoài chưa sẵn sàng."


async def load_gmail_details(service: Any, summaries: list[Any]) -> list[dict[str, Any]]:
    """Fetch full email details in parallel."""
    get_message = getattr(service, "get_message", None)
    if get_message is None:
        return [serialize_message(item) for item in summaries]

    async def _one(summary: Any) -> Any:
        identifier = getattr(summary, "id", "")
        if not identifier:
            return summary
        try:
            return await get_message(identifier, format="metadata")
        except AppError:
            return summary

    details = await asyncio.gather(*[_one(item) for item in summaries])
    return [serialize_message(item) for item in details]


def serialize_message(message: Any) -> dict[str, Any]:
    """Serialize a Gmail message into dictionary format."""
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
        "when": iso(internal_date) if isinstance(internal_date, datetime) else "",
        "snippet": getattr(message, "snippet", "") or "",
        "unread": "UNREAD" in {label.upper() for label in labels},
    }


def serialize_event(event: Any) -> dict[str, Any]:
    """Serialize a Calendar event into dictionary format."""
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


# Backwards compatibility aliases
_apply_period = apply_period
_as_local = as_local
_blocked_message = blocked_message
_iso = iso
_load_gmail_details = load_gmail_details
_serialize_event = serialize_event
_serialize_message = serialize_message

__all__ = [
    "_apply_period",
    "_as_local",
    "_blocked_message",
    "_iso",
    "_load_gmail_details",
    "_serialize_event",
    "_serialize_message",
    "apply_period",
    "as_local",
    "blocked_message",
    "build_gmail_search_query",
    "calendar_mutation_kind",
    "infer_calendar_window",
    "infer_event_summary",
    "infer_past_calendar_window",
    "is_communication_draft",
    "is_communication_mutation",
    "iso",
    "load_gmail_details",
    "parse_event_times",
    "retrieval_requester_id",
    "serialize_event",
    "serialize_message",
]
