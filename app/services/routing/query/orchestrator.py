"""Natural-language query orchestration for the HTTP `/query` path.

FastTriage still chooses the route. Execution here uses live Calendar / Gmail /
retrieval services instead of the unwired default harness dispatcher.
Mutations never hit Google directly: they become approval requests.
The class is the last link of a mixin chain (see base.py): follow-ups,
knowledge, specialists, streaming.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.declarations import (
    CALENDAR_AGENT_NAME,
    COMMUNICATION_AGENT_NAME,
    KNOWLEDGE_RESEARCH_AGENT_NAME,
)
from app.domain.enums import RouteType, RunStatus, is_supervisor_route, is_workflow_route
from app.domain.errors import (
    AuthenticationError,
    ExternalServiceError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.models.routing.route import RouteDecision
from app.services.platform.run_persistence import RunPersistenceService

from .email_synthesis import (
    compose_email_draft,
    fallback_summarize_emails,
    summarize_emails,
)
from .followup import (
    FOLLOWUP_PATTERNS,
    answer_anchor,
    condense_query_with_history,
    is_followup_query,
    standalone_followup,
)
from .models import (
    GOOGLE_CONNECT_HINT,
    QueryResult,
    QueryRouteInfo,
    route_info,
)
from .parsing import (
    _blocked_message,
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
        history = await self._load_conversation_history(session, conversation_id, user_id)
        effective_query, decision, anchor = await self._triage_in_context(query, history)
        await self._set_followup_scope(session, conversation_id, user_id, anchor)
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

        document_result = await self._maybe_handle_document(
            session, user_id, query, effective_query, run_id, decision, conversation_id
        )
        if document_result is not None:
            await self._complete(session, run_id, started, status=RunStatus.COMPLETED)
            return document_result

        try:
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
        conversation_history: list[dict[str, str]] | None = None,
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
            return self._fallback_workflow_result(run_id, decision, route)
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
            return await self._handle_knowledge(
                user_id,
                query,
                run_id,
                route,
                conversation_history=conversation_history,
                session=session,
            )
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
