"""Deterministic Google Calendar tool declarations and execution wrappers."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import date, datetime
from datetime import time as time_value
from typing import TYPE_CHECKING, Any

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.errors import AppError, PermissionDeniedError, ValidationError
from app.domain.models import (
    CalendarAttendee,
    CalendarEventRequest,
    CalendarEventUpdate,
    ToolContext,
    ToolDefinition,
    ToolExecutionMetadata,
    ToolInput,
    ToolResult,
    normalize_aware_datetime,
    parse_calendar_value,
)
from app.integrations.google_calendar import CALENDAR_READONLY_SCOPE, CALENDAR_SCOPE
from app.services.approvals import (
    STALE_CHECK_ETAG_TOOLS,
    expected_target_fingerprint_from_arguments,
    require_mutation_approval,
)
from app.services.google.calendar import CalendarService
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _tool(
    name: str,
    description: str,
    *,
    capabilities: list[str],
    parameters_schema: dict[str, Any],
    required_scopes: list[str],
    action_class: ActionClass = ActionClass.READ,
    risk_level: ActionRiskLevel = ActionRiskLevel.READ_ONLY,
    is_mutation: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        category="calendar",
        capabilities=capabilities,
        parameters_schema=parameters_schema,
        required_scopes=required_scopes,
        action_class=action_class,
        risk_level=risk_level,
        is_mutation=is_mutation,
    )


_CALENDAR_READ = [CALENDAR_READONLY_SCOPE]
_CALENDAR_WRITE = [CALENDAR_SCOPE]
_STRING = {"type": "string"}
_EXPECTED_ETAG = {
    "type": "string",
    "description": (
        "ETag from the last read of this event. Required for stale-target protection "
        "on update/delete/attendee mutations. Omitting it is rejected (fail-closed) "
        "and the same ETag is sent to Google as If-Match."
    ),
}
_DATETIME = {"type": "string", "format": "date-time"}
_DATE_OR_DATETIME = {"type": "string", "description": "ISO date or timezone-aware RFC3339 datetime"}
_ATTENDEES = {"type": "array", "items": {"type": "object"}}
_CALENDAR_IDS = {"type": "array", "items": {"type": "string"}}


CALENDAR_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    _tool(
        "calendar.list_events",
        "List normalized Google Calendar events with deterministic pagination and timezone-aware bounds.",
        capabilities=["calendar.read", "calendar.events"],
        parameters_schema={
            "type": "object",
            "properties": {
                "calendar_id": _STRING,
                "time_min": _DATE_OR_DATETIME,
                "time_max": _DATE_OR_DATETIME,
                "page_size": {"type": "integer", "minimum": 1, "maximum": 2500},
                "page_token": _STRING,
                "time_zone": _STRING,
                "show_deleted": {"type": "boolean"},
            },
        },
        required_scopes=_CALENDAR_READ,
    ),
    _tool(
        "calendar.search_events",
        "Search Google Calendar event text without introducing an LLM decision point.",
        capabilities=["calendar.read", "calendar.search"],
        parameters_schema={
            "type": "object",
            "properties": {
                "query": _STRING,
                "calendar_id": _STRING,
                "time_min": _DATE_OR_DATETIME,
                "time_max": _DATE_OR_DATETIME,
                "page_size": {"type": "integer", "minimum": 1, "maximum": 2500},
                "page_token": _STRING,
                "time_zone": _STRING,
                "show_deleted": {"type": "boolean"},
            },
            "required": ["query"],
        },
        required_scopes=_CALENDAR_READ,
    ),
    _tool(
        "calendar.get_event",
        "Fetch one normalized Google Calendar event.",
        capabilities=["calendar.read", "calendar.events"],
        parameters_schema={
            "type": "object",
            "properties": {"event_id": _STRING, "calendar_id": _STRING},
            "required": ["event_id"],
        },
        required_scopes=_CALENDAR_READ,
    ),
    _tool(
        "calendar.get_free_busy",
        "Fetch normalized busy intervals for one or more calendars.",
        capabilities=["calendar.read", "calendar.availability"],
        parameters_schema={
            "type": "object",
            "properties": {
                "calendar_ids": _CALENDAR_IDS,
                "time_min": _DATETIME,
                "time_max": _DATETIME,
                "time_zone": _STRING,
            },
            "required": ["time_min", "time_max"],
        },
        required_scopes=_CALENDAR_READ,
    ),
    _tool(
        "calendar.find_free_slots",
        "Find deterministic conflict-free Calendar slots using fixed interval arithmetic.",
        capabilities=["calendar.read", "calendar.availability", "calendar.slot_finding"],
        parameters_schema={
            "type": "object",
            "properties": {
                "calendar_ids": _CALENDAR_IDS,
                "window_start": _DATETIME,
                "window_end": _DATETIME,
                "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                "slot_step_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                "max_results": {"type": "integer", "minimum": 1},
                "time_zone": _STRING,
            },
            "required": ["window_start", "window_end", "duration_minutes"],
        },
        required_scopes=_CALENDAR_READ,
    ),
    _tool(
        "calendar.create_event",
        "Create a Google Calendar event; attendee invitations are external communication.",
        capabilities=["calendar.write", "calendar.events", "calendar.invite"],
        parameters_schema={
            "type": "object",
            "properties": {
                "calendar_id": _STRING,
                "summary": _STRING,
                "start": _DATE_OR_DATETIME,
                "end": _DATE_OR_DATETIME,
                "time_zone": _STRING,
                "all_day": {"type": "boolean"},
                "description": _STRING,
                "location": _STRING,
                "attendees": _ATTENDEES,
                "send_updates": {"type": "string", "enum": ["all", "externalOnly", "none"]},
            },
            "required": ["start", "end"],
        },
        required_scopes=_CALENDAR_WRITE,
        action_class=ActionClass.EXTERNAL_COMMUNICATION,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "calendar.update_event",
        "Update a Google Calendar event; changes may notify attendees.",
        capabilities=["calendar.write", "calendar.events", "calendar.invite"],
        parameters_schema={
            "type": "object",
            "properties": {
                "event_id": _STRING,
                "calendar_id": _STRING,
                "summary": _STRING,
                "start": _DATE_OR_DATETIME,
                "end": _DATE_OR_DATETIME,
                "time_zone": _STRING,
                "all_day": {"type": "boolean"},
                "description": _STRING,
                "location": _STRING,
                "attendees": _ATTENDEES,
                "send_updates": {"type": "string", "enum": ["all", "externalOnly", "none"]},
                "expected_etag": _EXPECTED_ETAG,
            },
            "required": ["event_id"],
        },
        required_scopes=_CALENDAR_WRITE,
        action_class=ActionClass.EXTERNAL_COMMUNICATION,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "calendar.delete_event",
        "Delete a Google Calendar event and optionally notify attendees.",
        capabilities=["calendar.write", "calendar.events", "calendar.delete"],
        parameters_schema={
            "type": "object",
            "properties": {
                "event_id": _STRING,
                "calendar_id": _STRING,
                "send_updates": {"type": "string", "enum": ["all", "externalOnly", "none"]},
                "expected_etag": _EXPECTED_ETAG,
            },
            "required": ["event_id"],
        },
        required_scopes=_CALENDAR_WRITE,
        action_class=ActionClass.DESTRUCTIVE,
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        is_mutation=True,
    ),
    _tool(
        "calendar.add_attendee",
        "Add an attendee to a Calendar event and classify the invitation as external communication.",
        capabilities=["calendar.write", "calendar.events", "calendar.invite"],
        parameters_schema={
            "type": "object",
            "properties": {
                "event_id": _STRING,
                "calendar_id": _STRING,
                "email": _STRING,
                "display_name": _STRING,
                "send_updates": {"type": "string", "enum": ["all", "externalOnly", "none"]},
                "expected_etag": _EXPECTED_ETAG,
            },
            "required": ["event_id", "email"],
        },
        required_scopes=_CALENDAR_WRITE,
        action_class=ActionClass.EXTERNAL_COMMUNICATION,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "calendar.remove_attendee",
        "Remove an attendee from a Calendar event and classify the change as external communication.",
        capabilities=["calendar.write", "calendar.events", "calendar.invite"],
        parameters_schema={
            "type": "object",
            "properties": {
                "event_id": _STRING,
                "calendar_id": _STRING,
                "email": _STRING,
                "send_updates": {"type": "string", "enum": ["all", "externalOnly", "none"]},
                "expected_etag": _EXPECTED_ETAG,
            },
            "required": ["event_id", "email"],
        },
        required_scopes=_CALENDAR_WRITE,
        action_class=ActionClass.EXTERNAL_COMMUNICATION,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        is_mutation=True,
    ),
)

_DEFINITIONS_BY_NAME = {definition.name: definition for definition in CALENDAR_TOOL_DEFINITIONS}


def calendar_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Return defensive copies of every P7 Calendar tool declaration."""
    return tuple(definition.model_copy(deep=True) for definition in CALENDAR_TOOL_DEFINITIONS)


def build_calendar_tool_registry() -> ToolRegistry:
    """Build a registry containing only Calendar tools."""
    return ToolRegistry(calendar_tool_definitions())


def _parse_datetime_argument(value: object, *, time_zone: str | None, label: str) -> datetime:
    try:
        parsed = parse_calendar_value(value)
        if isinstance(parsed, date) and not isinstance(parsed, datetime):
            if not time_zone:
                raise ValueError(f"{label} date values require time_zone.")
            parsed = datetime.combine(parsed, time_value.min)
        if not isinstance(parsed, datetime):
            raise ValueError(f"{label} must be a datetime.")
        return normalize_aware_datetime(parsed, time_zone=time_zone)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid Calendar {label}.") from exc


def _tool_time_zone(arguments: dict[str, Any]) -> str | None:
    """Accept both Calendar wire ``time_zone`` and proposal ``timezone``."""
    raw = arguments.get("time_zone") or arguments.get("timezone")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _event_request(arguments: dict[str, Any]) -> CalendarEventRequest:
    start = arguments.get("start")
    end = arguments.get("end")
    if start is None or end is None:
        raise ValidationError("Calendar event start and end are required.")
    try:
        return CalendarEventRequest.from_values(
            summary=str(arguments.get("summary") or ""),
            start=start,
            end=end,
            time_zone=_tool_time_zone(arguments),
            all_day=arguments.get("all_day"),
            description=arguments.get("description"),
            location=arguments.get("location"),
            attendees=arguments.get("attendees", []),
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid Calendar event request.") from exc


def _event_update(arguments: dict[str, Any]) -> CalendarEventUpdate:
    try:
        has_attendees = "attendees" in arguments
        return CalendarEventUpdate.from_values(
            summary=arguments.get("summary"),
            start=arguments.get("start"),
            end=arguments.get("end"),
            time_zone=_tool_time_zone(arguments),
            all_day=arguments.get("all_day"),
            description=arguments.get("description"),
            location=arguments.get("location"),
            attendees=arguments.get("attendees") if has_attendees else None,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid Calendar event update.") from exc


class GoogleCalendarTools:
    """Expose typed Calendar service calls as normalized ToolResult values."""

    def __init__(self, service: CalendarService) -> None:
        self.service = service

    @classmethod
    async def for_user(
        cls,
        oauth_service,
        session: AsyncSession,
        user_id: str,
        *,
        tool_name: str | None = None,
        required_scopes: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> GoogleCalendarTools:
        if required_scopes is None and tool_name is not None:
            definition = _DEFINITIONS_BY_NAME.get(tool_name)
            if definition is None:
                raise ValidationError("Calendar tool is not registered.")
            required_scopes = definition.required_scopes
        service = await CalendarService.for_user(
            oauth_service,
            session,
            user_id,
            required_scopes=required_scopes,
            **kwargs,
        )
        return cls(service)

    async def execute(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Execute a declared Calendar operation without an LLM decision point."""
        started = time.perf_counter()
        self.service.calendar.begin_operation()
        try:
            definition = _DEFINITIONS_BY_NAME.get(tool_input.tool_name)
            if definition is None:
                return self._failure(
                    tool_input.tool_name,
                    "Calendar tool is not registered.",
                    started=started,
                )
            if definition.is_mutation:
                if context.read_only_view:
                    raise PermissionDeniedError("Read-only tool views cannot execute mutations.")
                current_fp = await self._live_etag_if_expected(tool_input)
                await require_mutation_approval(
                    tool_name=tool_input.tool_name,
                    context_token=context.approval_token,
                    arguments=tool_input.arguments,
                    run_id=context.run_id,
                    user_id=context.user_id,
                    delegation=context.delegation,
                    consume=True,
                    current_target_fingerprint=current_fp,
                )
            output = await self._dispatch(tool_input.tool_name, tool_input.arguments)
            return ToolResult(
                tool_name=tool_input.tool_name,
                success=True,
                output=output,
                metadata=self._metadata(tool_input.tool_name, started),
            )
        except AppError as exc:
            return self._failure(tool_input.tool_name, exc.message, started=started)
        except Exception:  # noqa: BLE001 - unexpected failures become ToolResult
            logger.exception(
                "Calendar tool execution failed", extra={"tool_name": tool_input.tool_name}
            )
            return self._failure(
                tool_input.tool_name,
                "Calendar tool execution failed.",
                started=started,
            )
        finally:
            self.service.calendar.finish_operation()

    async def _live_etag_if_expected(self, tool_input: ToolInput) -> str | None:
        """Fetch current Calendar ETag for stale-check (M3, H3)."""
        if tool_input.tool_name not in STALE_CHECK_ETAG_TOOLS:
            return None
        if expected_target_fingerprint_from_arguments(tool_input.arguments) is None:
            return None
        event_id = str(tool_input.arguments.get("event_id") or "")
        if not event_id:
            return None
        try:
            current = await self.service.get_event(
                event_id, str(tool_input.arguments.get("calendar_id") or "primary")
            )
            return current.etag
        except (AppError, OSError, TimeoutError, TypeError, ValueError):
            return None

    async def invoke(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Alias used by generic tool runtimes."""
        return await self.execute(tool_input, context)

    async def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        time_zone = _tool_time_zone(args)
        if name == "calendar.list_events":
            time_min = (
                _parse_datetime_argument(args["time_min"], time_zone=time_zone, label="time_min")
                if args.get("time_min") is not None
                else None
            )
            time_max = (
                _parse_datetime_argument(args["time_max"], time_zone=time_zone, label="time_max")
                if args.get("time_max") is not None
                else None
            )
            return await self.service.list_events(
                str(args.get("calendar_id") or "primary"),
                time_min=time_min,
                time_max=time_max,
                page_size=int(args.get("page_size", 100)),
                page_token=args.get("page_token"),
                time_zone=time_zone,
                show_deleted=bool(args.get("show_deleted", False)),
            )
        if name == "calendar.search_events":
            time_min = (
                _parse_datetime_argument(args["time_min"], time_zone=time_zone, label="time_min")
                if args.get("time_min") is not None
                else None
            )
            time_max = (
                _parse_datetime_argument(args["time_max"], time_zone=time_zone, label="time_max")
                if args.get("time_max") is not None
                else None
            )
            return await self.service.search_events(
                str(args.get("query") or ""),
                str(args.get("calendar_id") or "primary"),
                time_min=time_min,
                time_max=time_max,
                page_size=int(args.get("page_size", 100)),
                page_token=args.get("page_token"),
                time_zone=time_zone,
                show_deleted=bool(args.get("show_deleted", False)),
            )
        if name == "calendar.get_event":
            return await self.service.get_event(
                args.get("event_id", ""), str(args.get("calendar_id") or "primary")
            )
        if name == "calendar.get_free_busy":
            start = _parse_datetime_argument(
                args.get("time_min"), time_zone=time_zone, label="time_min"
            )
            end = _parse_datetime_argument(
                args.get("time_max"), time_zone=time_zone, label="time_max"
            )
            return await self.service.get_free_busy(
                start,
                end,
                args.get("calendar_ids", ["primary"]),
                time_zone=time_zone,
            )
        if name == "calendar.find_free_slots":
            raw_start = (
                args.get("window_start")
                if args.get("window_start") is not None
                else args.get("time_min")
            )
            raw_end = (
                args.get("window_end")
                if args.get("window_end") is not None
                else args.get("time_max")
            )
            start = _parse_datetime_argument(raw_start, time_zone=time_zone, label="window_start")
            end = _parse_datetime_argument(raw_end, time_zone=time_zone, label="window_end")
            return await self.service.find_free_slots(
                start,
                end,
                int(args.get("duration_minutes", 0)),
                calendar_ids=args.get("calendar_ids", ["primary"]),
                slot_step_minutes=(
                    int(args["slot_step_minutes"])
                    if args.get("slot_step_minutes") is not None
                    else None
                ),
                max_results=int(args.get("max_results", 20)),
                time_zone=time_zone,
            )
        if name == "calendar.create_event":
            return await self.service.create_event(
                _event_request(args),
                str(args.get("calendar_id") or "primary"),
                send_updates=str(args.get("send_updates") or "all"),
            )
        if name == "calendar.update_event":
            return await self.service.update_event(
                args.get("event_id", ""),
                _event_update(args),
                str(args.get("calendar_id") or "primary"),
                send_updates=str(args.get("send_updates") or "all"),
                if_match=args.get("expected_etag"),
            )
        if name == "calendar.delete_event":
            return await self.service.delete_event(
                args.get("event_id", ""),
                str(args.get("calendar_id") or "primary"),
                send_updates=str(args.get("send_updates") or "all"),
                if_match=args.get("expected_etag"),
            )
        if name == "calendar.add_attendee":
            try:
                attendee = CalendarAttendee(
                    email=str(args.get("email") or ""),
                    display_name=args.get("display_name"),
                )
            except (TypeError, ValueError) as exc:
                raise ValidationError("Invalid Calendar attendee.") from exc
            return await self.service.add_attendee(
                args.get("event_id", ""),
                attendee,
                str(args.get("calendar_id") or "primary"),
                send_updates=str(args.get("send_updates") or "all"),
                if_match=args.get("expected_etag"),
            )
        if name == "calendar.remove_attendee":
            return await self.service.remove_attendee(
                args.get("event_id", ""),
                args.get("email", ""),
                str(args.get("calendar_id") or "primary"),
                send_updates=str(args.get("send_updates") or "all"),
                if_match=args.get("expected_etag"),
            )
        raise ValidationError("Calendar tool is not registered.")

    def _metadata(self, tool_name: str, started: float) -> ToolExecutionMetadata:
        return ToolExecutionMetadata(
            tool_name=tool_name,
            latency_ms=(time.perf_counter() - started) * 1000,
            retry_count=self.service.calendar.last_operation_retry_count,
        )

    def _failure(
        self,
        tool_name: str,
        message: str,
        *,
        started: float | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            error=message,
            metadata=self._metadata(tool_name, started or time.perf_counter()),
        )


__all__ = [
    "CALENDAR_TOOL_DEFINITIONS",
    "GoogleCalendarTools",
    "build_calendar_tool_registry",
    "calendar_tool_definitions",
]
