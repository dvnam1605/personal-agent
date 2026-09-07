"""CalendarAgent domain layer (spec P12B).

Deterministic helpers around the P11 runtime for the CalendarAgent:
system preamble, Direct schedule-query builder, deterministic slot-search
builder, and fail-closed event proposals.

No LLM calls, no network, no tool execution here: these builders produce
:class:`SpecialistTask` / :class:`ProposedAction` values that the P11
``SpecialistRunner`` and the approval workflow consume.
"""

from __future__ import annotations

import re
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
    "answer in a SINGLE turn from the pre-resolved context_data without tool "
    "calls. If context is missing, report needs_more_context — never guess. "
    "(Per P11-06, any tool call in DIRECT escalates to ReAct instead of "
    "executing, so multi-step lookups belong to BOUNDED_REACT tasks.)",
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
    """Build the DIRECT task for "Lịch hôm nay / ngày mai" (spec P12 §4.1).

    DIRECT answers in one turn from pre-resolved ``context_data`` (event
    payloads) and makes no tool calls; live queries belong to
    :func:`complex_task`.
    """
    label = _require_text(window_label, "window_label")
    start = _require_iso_time(time_min, "time_min")
    end = _require_iso_time(time_max, "time_max")
    _require_ordered_window(start, end, time_min, time_max)
    zone = _require_text(timezone, "timezone")
    return SpecialistTask(
        agent_name=CALENDAR_AGENT_NAME,
        goal=(
            f"Báo cáo lịch {label} ({start} → {end}, múi giờ {zone}) DỰA VÀO "
            "context_data có sẵn, trong MỘT lượt duy nhất, KHÔNG gọi tool. "
            "Thiếu context thì báo needs_more_context, không đoán."
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
    duration = _require_positive_int(duration_minutes, "duration_minutes")
    start = _require_iso_time(time_min, "time_min")
    end = _require_iso_time(time_max, "time_max")
    _require_ordered_window(start, end, time_min, time_max)
    attendees = (
        [] if attendee_emails is None else _require_str_list(attendee_emails, "attendee_emails")
    )
    work_start = _require_working_hour(working_hours_start, "working_hours_start")
    work_end = _require_working_hour(working_hours_end, "working_hours_end")
    if work_end <= work_start:
        raise ValidationError(
            "Working hours must satisfy working_hours_end > working_hours_start.",
            details={
                "working_hours_start": working_hours_start,
                "working_hours_end": working_hours_end,
            },
        )
    preference = _require_text(preferred_time_of_day, "preferred_time_of_day")
    zone = _require_text(timezone, "timezone")
    return SpecialistTask(
        agent_name=CALENDAR_AGENT_NAME,
        goal=(
            f"Tìm slot trống {duration} phút"
            + (f" với {', '.join(attendees)}" if attendees else "")
            + ": B1 lấy busy windows bằng calendar.get_free_busy, "
            "B2 gọi calendar.find_free_slots với "
            f"duration_minutes={duration}, window_start={start}, "
            f"window_end={end}, working_hours_start={work_start}, "
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
    duration = _require_positive_int(duration_minutes, "duration_minutes")
    label = _require_text(window_label, "window_label")
    return SpecialistReport(
        status=SpecialistStatus.SUCCESS,
        summary=(
            f"Không còn slot trống {duration} phút trong {label} theo thuật toán find_free_slots."
        ),
        data={"duration_minutes": duration, "window_label": label},
    )


_EVENT_ACTION_TOOLS = {
    "create_event": "calendar.create_event",
    "update_event": "calendar.update_event",
    "delete_event": "calendar.delete_event",
    "add_attendee": "calendar.add_attendee",
    "remove_attendee": "calendar.remove_attendee",
}

_EVENT_ACTION_DESCRIPTIONS = {
    "create_event": "Đặt lịch",
    "update_event": "Cập nhật lịch",
    "delete_event": "Xóa lịch",
    "add_attendee": "Thêm khách mời vào lịch",
    "remove_attendee": "Xóa khách mời khỏi lịch",
}


def build_event_proposal(
    *,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    attendee_emails: list[str] | None = None,
    location: str | None = None,
    description: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    event_id: str | None = None,
    action_type: str = "create_event",
) -> ProposedAction:
    """Wrap a calendar mutation intent as a fail-closed proposal (spec P12 §4.1).

    The proposal targets the real mutation tool for the intent. ``create_event``
    needs summary/start/end; ``update_event`` needs ``event_id`` plus at least
    one changed field; ``delete_event`` needs only ``event_id``; attendee
    actions need ``event_id`` plus attendees. Deletion is IRREVERSIBLE; the
    rest are HIGH_IMPACT_WRITE.
    """
    try:
        tool_name = _EVENT_ACTION_TOOLS[action_type]
        action_verb = _EVENT_ACTION_DESCRIPTIONS[action_type]
    except (KeyError, TypeError) as exc:
        raise ValidationError(
            f"Unknown event action type: {action_type!r}.",
            details={"action_type": str(action_type)},
        ) from exc
    zone = _require_text(timezone, "timezone")
    attendees = (
        [] if attendee_emails is None else _require_str_list(attendee_emails, "attendee_emails")
    )
    parameters: dict[str, object] = {"attendees": attendees, "timezone": zone}
    if action_type in ("update_event", "delete_event", "add_attendee", "remove_attendee"):
        parameters["event_id"] = _require_text(event_id or "", "event_id")
    changed_fields = 0
    if action_type in ("create_event", "update_event"):
        if summary is None and action_type == "create_event":
            raise ValidationError(
                "Event proposal requires a summary for create_event.",
                details={"action_type": action_type},
            )
        if summary is not None:
            parameters["summary"] = _require_text(summary, "summary")
            changed_fields += 1
        if action_type == "update_event" and ((start is None) != (end is None)):
            raise ValidationError(
                "Event update requires both start and end times when updating schedule.",
                details={"start": start, "end": end},
            )
        if start is not None or end is not None or action_type == "create_event":
            clean_start = _require_iso_time(start or "", "start")
            clean_end = _require_iso_time(end or "", "end")
            _require_ordered_window(clean_start, clean_end, start, end)
            parameters["start"] = clean_start
            parameters["end"] = clean_end
            changed_fields += 1
    if action_type in ("create_event", "update_event", "add_attendee", "remove_attendee"):
        if action_type in ("add_attendee", "remove_attendee") and not attendees:
            raise ValidationError(
                f"Event proposal requires attendees for {action_type}.",
                details={"action_type": action_type},
            )
    if location is not None:
        parameters["location"] = _require_text(location, "location")
        changed_fields += 1
    if description is not None:
        parameters["description"] = sanitize_string(
            _require_text(description, "description"),
            max_string_len=PROPOSAL_DESCRIPTION_LEN,
        )
        changed_fields += 1
    if action_type == "update_event" and changed_fields == 0:
        raise ValidationError(
            "Update proposal requires at least one changed field.",
            details={"action_type": action_type},
        )
    subject = str(parameters.get("summary", parameters.get("event_id", "")))
    attendee_text = f" với {', '.join(attendees)}" if attendees else ""
    risk_level = (
        ActionRiskLevel.IRREVERSIBLE
        if action_type == "delete_event"
        else ActionRiskLevel.HIGH_IMPACT_WRITE
    )
    return ProposedAction(
        action_type=action_type,
        description=f"{action_verb} '{subject}' (múi giờ {zone}){attendee_text}.",
        tool_name=tool_name,
        parameters=parameters,
        risk_level=risk_level,
        requires_approval=True,
    )


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"CalendarAgent input '{field}' must be a non-blank string.",
            details={"field": field},
        )
    return value.strip()


def _require_str_list(value: object, field: str) -> list[str]:
    if isinstance(value, str) or not isinstance(value, list):
        raise ValidationError(
            f"CalendarAgent input '{field}' must be a list of non-blank strings.",
            details={"field": field, "received_type": type(value).__name__},
        )
    return [_require_text(item, f"{field}[]") for item in value]


def _require_positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValidationError(
            f"CalendarAgent input '{field}' must be a positive integer.",
            details={"field": field},
        )
    if isinstance(value, float):
        if not value.is_integer():
            raise ValidationError(
                f"CalendarAgent input '{field}' must be a positive integer.",
                details={"field": field},
            )
        value = int(value)
    if not isinstance(value, int) or value < 1:
        raise ValidationError(
            f"CalendarAgent input '{field}' must be a positive integer.",
            details={"field": field},
        )
    return value


_WORKING_HOUR_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _require_working_hour(value: str, field: str) -> str:
    text = _require_text(value, field)
    if _WORKING_HOUR_RE.match(text) is None:
        raise ValidationError(
            f"CalendarAgent input '{field}' must be HH:MM (00:00-23:59).",
            details={"field": field, "value": text},
        )
    return text


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


def _require_ordered_window(start: str, end: str, raw_start: object, raw_end: object) -> None:
    """Compare parsed datetimes (lexical compare breaks across mixed offsets)."""
    parsed_start = datetime.fromisoformat(start)
    parsed_end = datetime.fromisoformat(end)
    if (parsed_start.tzinfo is None) != (parsed_end.tzinfo is None):
        raise ValidationError(
            "Calendar window bounds must consistently include or omit timezone info.",
            details={"time_min": raw_start, "time_max": raw_end},
        )
    if parsed_end <= parsed_start:
        raise ValidationError(
            "Calendar window must satisfy time_max > time_min.",
            details={"time_min": raw_start, "time_max": raw_end},
        )


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
