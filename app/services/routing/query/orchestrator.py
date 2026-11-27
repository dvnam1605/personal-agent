"""Natural-language query orchestration for the HTTP `/query` path.

FastTriage still chooses the route. Execution here uses live Calendar / Gmail /
retrieval services instead of the unwired default harness dispatcher.
Mutations never hit Google directly: they become approval requests.
The class is the last link of a mixin chain (see base.py): follow-ups,
knowledge, commands, specialists, streaming. Chat commands and routed requests run
through the same helpers as the SSE path (``_handle_command``, ``_dispatch_routed``).
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.declarations import KNOWLEDGE_RESEARCH_AGENT_NAME
from app.domain.enums import RouteType, RunStatus
from app.domain.errors import (
    AuthenticationError,
    ExternalServiceError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.models.routing.route import RouteDecision
from app.services.platform.run_persistence import RunPersistenceService

from .followup import (
    FOLLOWUP_PATTERNS,
    answer_anchor,
    condense_query_with_history,
    is_followup_query,
    standalone_followup,
)
from .mail.email_synthesis import (
    compose_email_draft,
    fallback_summarize_emails,
    summarize_emails,
)
from .models import (
    GOOGLE_CONNECT_HINT,
    QueryResult,
    QueryRouteInfo,
    route_info,
)
from .parsing import (
    blocked_message,
    build_gmail_search_query,
    calendar_mutation_kind,
    infer_calendar_window,
    infer_event_summary,
    infer_past_calendar_window,
    is_communication_draft,
    is_communication_mutation,
    parse_event_times,
    retrieval_requester_id,
)
from .specialists import fixed_reply
from .streaming import StreamMixin


class QueryOrchestrator(StreamMixin):
    """Triage a natural-language query and execute the matching live specialist."""

    async def handle(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        *,
        correlation_id: str | None = None,
        conversation_id: str | None = None,
    ) -> QueryResult:
        """Persist a run, execute the routed specialist, and return a QueryResult."""
        started = time.perf_counter()
        resume = await self._resume_pending(session, conversation_id, user_id, query)
        resumed = resume.query != query
        query = resume.query
        history = await self._load_conversation_history(session, conversation_id, user_id)
        # A resumed request is complete on its own; it must not be re-read as a follow-up.
        effective_query, decision, anchor = await self._triage_in_context(
            query, None if resumed else history
        )
        await self._set_followup_scope(session, conversation_id, user_id, anchor)
        await self.ensure_user(session, user_id)
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
        if resume.message is not None:
            await self._complete(session, run_id, started, status=RunStatus.COMPLETED)
            return QueryResult(
                run_id=run_id,
                status="completed",
                message=resume.message,
                route=route_info(decision),
            )

        try:
            result = await self._handle_command(
                session, user_id, query, effective_query, run_id, decision, conversation_id
            )
            if result is None:
                result = await self._dispatch(
                    session,
                    user_id,
                    effective_query,
                    run_id,
                    decision,
                    conversation_history=history,
                )
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
                message=blocked_message(exc),
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

        await self._remember_pending(session, conversation_id, user_id, result)
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
        conversation_history: list[dict[str, str]] | None = None,
    ) -> QueryResult:
        """Answer a routed request: canned replies, knowledge, then the shared dispatch."""
        route = route_info(decision)
        fixed = fixed_reply(decision.route_type)
        if fixed is not None:
            status, message = fixed
            return QueryResult(run_id=run_id, status=status, message=message, route=route)
        if (
            decision.route_type is RouteType.DIRECT_SPECIALIST
            and decision.target_agent == KNOWLEDGE_RESEARCH_AGENT_NAME
        ):
            return await self._handle_knowledge(
                user_id,
                query,
                run_id,
                route,
                conversation_history=conversation_history,
                session=session,
            )
        return await self._dispatch_routed(session, user_id, query, run_id, decision, route)


__all__ = [
    "FOLLOWUP_PATTERNS",
    "GOOGLE_CONNECT_HINT",
    "QueryOrchestrator",
    "QueryResult",
    "QueryRouteInfo",
    "answer_anchor",
    "build_gmail_search_query",
    "calendar_mutation_kind",
    "compose_email_draft",
    "condense_query_with_history",
    "fallback_summarize_emails",
    "infer_calendar_window",
    "infer_event_summary",
    "infer_past_calendar_window",
    "is_communication_draft",
    "is_communication_mutation",
    "is_followup_query",
    "parse_event_times",
    "retrieval_requester_id",
    "route_info",
    "standalone_followup",
    "summarize_emails",
]
