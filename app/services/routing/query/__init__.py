"""Query orchestration package for natural language request processing.

Core modules (base, models, parsing, followup, knowledge, specialists, streaming,
orchestrator) live here; chat commands are in ``commands/`` and email work in ``mail/``.
The moved modules stay importable under their old flat paths
(``app.services.routing.query.drafting`` …) through ``sys.modules`` aliases.
"""

import sys

from .commands import analysis, goals, memory, minutes, routines, tasks, web
from .mail import clarification, drafting, email_synthesis
from .mail.email_synthesis import (
    compose_email_draft,
    fallback_summarize_emails,
    summarize_emails,
    summarize_emails_stream,
)
from .models import (
    GOOGLE_CONNECT_HINT,
    CalendarFactory,
    ClockFn,
    CommunicationFactory,
    QueryResult,
    QueryRouteInfo,
    RetrieveFn,
    RunIdFn,
    SummarizeEmailsFn,
    route_info,
)
from .orchestrator import QueryOrchestrator
from .parsing import (
    as_local,
    blocked_message,
    build_gmail_search_query,
    calendar_mutation_kind,
    infer_calendar_window,
    infer_event_summary,
    infer_past_calendar_window,
    is_communication_draft,
    is_communication_mutation,
    iso,
    load_gmail_details,
    parse_event_times,
    retrieval_requester_id,
    serialize_event,
    serialize_message,
)

__all__ = [
    "GOOGLE_CONNECT_HINT",
    "CalendarFactory",
    "ClockFn",
    "CommunicationFactory",
    "QueryOrchestrator",
    "QueryResult",
    "QueryRouteInfo",
    "RetrieveFn",
    "RunIdFn",
    "SummarizeEmailsFn",
    "as_local",
    "blocked_message",
    "build_gmail_search_query",
    "calendar_mutation_kind",
    "compose_email_draft",
    "fallback_summarize_emails",
    "infer_calendar_window",
    "infer_event_summary",
    "infer_past_calendar_window",
    "is_communication_draft",
    "is_communication_mutation",
    "iso",
    "load_gmail_details",
    "parse_event_times",
    "retrieval_requester_id",
    "route_info",
    "serialize_event",
    "serialize_message",
    "summarize_emails",
    "summarize_emails_stream",
]

for _module in (
    analysis,
    goals,
    memory,
    minutes,
    routines,
    tasks,
    web,
    clarification,
    drafting,
    email_synthesis,
):
    sys.modules.setdefault(f"{__name__}.{_module.__name__.rsplit('.', 1)[-1]}", _module)
del _module
