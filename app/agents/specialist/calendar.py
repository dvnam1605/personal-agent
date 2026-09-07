"""CalendarAgent domain layer (spec P12B).

Deterministic helpers around the P11 runtime for the CalendarAgent:
system preamble, Direct schedule-query builder, deterministic slot-search
builder, and fail-closed event proposals.

No LLM calls, no network, no tool execution here: these builders produce
:class:`SpecialistTask` / :class:`ProposedAction` values that the P11
``SpecialistRunner`` and the approval workflow consume.
"""

from __future__ import annotations

from datetime import datetime

from app.agents.declarations import CALENDAR_AGENT_NAME
from app.core.sanitization import sanitize_string
from app.domain.enums import ActionRiskLevel, ExecutionMode, SpecialistStatus
from app.domain.errors import ValidationError
from app.domain.models import (
    ExecutionBudget,
    ProposedAction,
    SpecialistReport,
    SpecialistTask,
)

CALENDAR_AGENT = CALENDAR_AGENT_NAME

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
DEFAULT_WORKING_HOURS_START = "08:30"
DEFAULT_WORKING_HOURS_END = "17:30"

P12_REACT_BUDGET = ExecutionBudget(
    max_llm_calls=5,
    max_tool_calls=10,
    max_react_steps=4,
    max_prompt_tokens=3000,
    max_total_tokens=4000,
)

CALENDAR_SYSTEM_PREAMBLE: tuple[str, ...] = (
    "You are the CalendarAgent: schedule querying, conflict detection, and "
    "deterministic free-slot calculation over Google Calendar.",
    "Exact schedule lookups (today, tomorrow, one event by id) run DIRECT: "
    "call the single read tool, then report.",
    "NEVER compute free slots with prompt-token arithmetic. Always fetch busy "
    "windows with calendar.get_free_busy, then delegate to "
    "calendar.find_free_slots with duration_minutes, the UTC window, working "
    "hours 08:30-17:30, and the preferred time of day.",
    "Booking, updating, deleting events, and attendee changes NEVER execute "
    "live. Return a ProposedAction with summary, times, attendees, and "
    "HIGH_IMPACT_WRITE risk for human approval.",
    "Mutation tools without an approval token fail closed; surface the "
    "PermissionDeniedError as a blocked report instead of retrying.",
)

PROPOSAL_DESCRIPTION_LEN = 500


def schedule_query_task(
    window_label: str,
    *,
    time_min: str,
    time_max: str,
    timezone: str = DEFAULT_TIMEZONE,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build the DIRECT task for "Lịch hôm nay / ngày mai" (spec P12 §4.1)."""
    label = _require_text(window_label, "window_label")
    start = _require_iso_time(time_min, "time_min")
    end = _require_iso_time(time_max, "time_max")
    if end <= start:
        raise ValidationError(
            "Calendar query window must satisfy time_max > time_min.",
            details={"time_min": time_min, "time_max": time_max},
        )
    zone = _require_text(timezone, "timezone")
    return SpecialistTask(
        agent_name=CALENDAR_AGENT_NAME,
        goal=(
            f"Liệt kê lịch {label} bằng calendar.list_events "
            f"(time_min={start}, time_max={end}, timezone={zone}) "
            "rồi báo cáo danh sách sự kiện qua specialist.report."
        ),
        mode=ExecutionMode.DIRECT,
        context_data=dict(context_data or {}),
        system_preamble=CALENDAR_SYSTEM_PREAMBLE,
    )


def slot_search_task(
    *,
    duration_minutes: int,
    time_min: str,
    time_max: str,
    attendee_emails: list[str] | None = None,
    working_hours_start: str = DEFAULT_WORKING_HOURS_START,
    working_hours_end: str = DEFAULT_WORKING_HOURS_END,
    preferred_time_of_day: str = "morning",
    timezone: str = DEFAULT_TIMEZONE,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build the Bounded ReAct slot task delegating to find_free_slots (§4.2)."""
    if duration_minutes <= 0:
        raise ValidationError(
            "Slot duration must be a positive number of minutes.",
            details={"duration_minutes": duration_minutes},
        )
    start = _require_iso_time(time_min, "time_min")
    end = _require_iso_time(time_max, "time_max")
    if end <= start:
        raise ValidationError(
            "Slot search window must satisfy time_max > time_min.",
            details={"time_min": time_min, "time_max": time_max},
        )
    attendees = [_require_text(email, "attendee_emails[]") for email in attendee_emails or []]
    work_start = _require_text(working_hours_start, "working_hours_start")
    work_end = _require_text(working_hours_end, "working_hours_end")
    preference = _require_text(preferred_time_of_day, "preferred_time_of_day")
    zone = _require_text(timezone, "timezone")
    return SpecialistTask(
        agent_name=CALENDAR_AGENT_NAME,
        goal=(
            f"Tìm slot trống {duration_minutes} phút"
            + (f" với {', '.join(attendees)}" if attendees else "")
            + ": B1 lấy busy windows bằng calendar.get_free_busy, "
            "B2 gọi calendar.find_free_slots với "
            f"duration_minutes={duration_minutes}, time_min={start}, "
            f"time_max={end}, working_hours_start={work_start}, "
            f"working_hours_end={work_end}, preferred_time_of_day={preference}, "
            f"timezone={zone}; trình bày kết quả tiếng Việt qua specialist.report. "
            "TUYỆT ĐỐI không tự tính slot bằng suy luận token."
        ),
        mode=ExecutionMode.BOUNDED_REACT,
        context_data=dict(context_data or {}),
        budget=P12_REACT_BUDGET,
        system_preamble=CALENDAR_SYSTEM_PREAMBLE,
    )


def complex_task(
    goal: str,
    *,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build a generic Bounded ReAct task capped by the P12 efficiency budget."""
    text = _require_text(goal, "goal")
    return SpecialistTask(
        agent_name=CALENDAR_AGENT_NAME,
        goal=text,
        mode=ExecutionMode.BOUNDED_REACT,
        context_data=dict(context_data or {}),
        budget=P12_REACT_BUDGET,
        system_preamble=CALENDAR_SYSTEM_PREAMBLE,
    )


def no_availability_report(*, duration_minutes: int, window_label: str) -> SpecialistReport:
    """Report a deterministic empty slot result without guessing alternatives."""
    return SpecialistReport(
        status=SpecialistStatus.SUCCESS,
        summary=(
            f"Không còn slot trống {duration_minutes} phút trong {window_label} "
            "theo thuật toán find_free_slots."
        ),
        data={"duration_minutes": duration_minutes, "window_label": window_label},
    )


def build_event_proposal(
    *,
    summary: str,
    start: str,
    end: str,
    attendee_emails: list[str] | None = None,
    location: str | None = None,
    description: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    action_type: str = "create_event",
) -> ProposedAction:
    """Wrap an event booking intent as a fail-closed proposal (spec P12 §4.1)."""
    clean_summary = _require_text(summary, "summary")
    clean_start = _require_iso_time(start, "start")
    clean_end = _require_iso_time(end, "end")
    if clean_end <= clean_start:
        raise ValidationError(
            "Event proposal must satisfy end > start.",
            details={"start": start, "end": end},
        )
    attendees = [_require_text(email, "attendee_emails[]") for email in attendee_emails or []]
    zone = _require_text(timezone, "timezone")
    parameters: dict[str, object] = {
        "summary": clean_summary,
        "start": clean_start,
        "end": clean_end,
        "attendees": attendees,
        "timezone": zone,
    }
    if location is not None:
        parameters["location"] = _require_text(location, "location")
    if description is not None:
        parameters["description"] = sanitize_string(
            _require_text(description, "description"),
            max_string_len=PROPOSAL_DESCRIPTION_LEN,
        )
    attendee_text = f" với {', '.join(attendees)}" if attendees else ""
    return ProposedAction(
        action_type=action_type,
        description=(
            f"Đặt lịch '{clean_summary}' từ {clean_start} đến {clean_end} "
            f"(múi giờ {zone}){attendee_text}."
        ),
        tool_name="calendar.create_event",
        parameters=parameters,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"CalendarAgent input '{field}' must be a non-blank string.",
            details={"field": field},
        )
    return value.strip()


def _require_iso_time(value: str, field: str) -> str:
    text = _require_text(value, field)
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(
            f"CalendarAgent input '{field}' must be ISO-8601.",
            details={"field": field, "value": text},
        ) from exc
    return text


__all__ = [
    "CALENDAR_AGENT",
    "CALENDAR_SYSTEM_PREAMBLE",
    "DEFAULT_TIMEZONE",
    "DEFAULT_WORKING_HOURS_END",
    "DEFAULT_WORKING_HOURS_START",
    "P12_REACT_BUDGET",
    "PROPOSAL_DESCRIPTION_LEN",
    "build_event_proposal",
    "complex_task",
    "no_availability_report",
    "schedule_query_task",
    "slot_search_task",
]
