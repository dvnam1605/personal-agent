"""Natural-language query orchestration for the HTTP `/query` path.

FastTriage still chooses the route. Execution here uses live Calendar / Gmail /
retrieval services instead of the unwired default harness dispatcher.
Mutations never hit Google directly: they become approval requests.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

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

from .email_synthesis import (
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
from .parsing import (
    _blocked_message,
    _iso,
    _load_gmail_details,
    _serialize_event,
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

logger = logging.getLogger(__name__)


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

    async def handle_stream(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        *,
        correlation_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Triage a query, persist run, stream tokens via SSE, and finalize run status."""
        import json

        started = time.perf_counter()
        decision = self._triage.triage(query)
        route = route_info(decision)
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

        def _sse(event: str, data: dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        # 1. Yield route metadata event
        yield _sse(
            "metadata",
            {
                "run_id": run_id,
                "status": "processing",
                "route": route.model_dump(mode="json"),
            },
        )

        final_status = RunStatus.COMPLETED
        final_error: str | None = None

        try:
            if decision.route_type is RouteType.REJECT:
                msg = "Yêu cầu bị từ chối vì vi phạm ranh giới an toàn."
                yield _sse("token", {"delta": msg})
                yield _sse("done", {"run_id": run_id, "status": "rejected"})
                return

            if decision.route_type is RouteType.CLARIFICATION:
                msg = (
                    "Bạn có thể nói rõ hơn được không? Ví dụ: xem lịch ngày mai, "
                    "hỏi tài liệu nội bộ, hoặc tạo một cuộc họp."
                )
                yield _sse("token", {"delta": msg})
                yield _sse("done", {"run_id": run_id, "status": "clarification_needed"})
                return

            if decision.route_type is RouteType.CASUAL_RESPONSE:
                msg = "Xin chào. Bạn cần tôi giúp gì?"
                yield _sse("token", {"delta": msg})
                yield _sse("done", {"run_id": run_id, "status": "casual_response"})
                return

            agent = decision.target_agent
            if agent == KNOWLEDGE_RESEARCH_AGENT_NAME:
                # Real LLM token streaming from RAG pipeline
                async for event in self._run_retrieval_stream(query, user_id):
                    ev_type = event.get("type")
                    if ev_type == "token":
                        delta = event.get("delta", "")
                        if delta:
                            yield _sse("token", {"delta": delta})
                    elif ev_type == "citations":
                        yield _sse(
                            "citations",
                            {
                                "citations": event.get("citations", []),
                                "sufficiency": event.get("status"),
                            },
                        )
                yield _sse("done", {"run_id": run_id, "status": "completed"})
                return

            # For other routes (Calendar, Email, Workflows), execute specialist
            result: QueryResult
            if is_workflow_route(decision.route_type) or is_supervisor_route(decision.route_type):
                handled = await self._handle_workflow(
                    session, user_id, query, run_id, decision, route
                )
                result = handled or QueryResult(
                    run_id=run_id,
                    status="routed",
                    message="Yêu cầu được định tuyến tới workflow nhưng chưa thực thi trên luồng này.",
                    route=route,
                )
            elif agent == CALENDAR_AGENT_NAME:
                result = await self._handle_calendar(session, user_id, query, run_id, route)
            elif agent == COMMUNICATION_AGENT_NAME:
                if is_communication_draft(query):
                    result = await self._handle_communication_draft(
                        session, user_id, query, run_id, route
                    )
                elif is_communication_mutation(query):
                    result = QueryResult(
                        run_id=run_id,
                        status="routed",
                        message=(
                            "Gửi hoặc xóa email trực tiếp không được thực hiện trên POST /query vì lý do an toàn. "
                            "Bạn có thể yêu cầu: 'Soạn email gửi [người nhận] về [nội dung]' để tôi soạn thảo trước và tạo thẻ duyệt cho bạn."
                        ),
                        route=route,
                    )
                else:
                    gmail_query, page_size = build_gmail_search_query(query)
                    service = await self._communication(session, user_id)
                    page = await service.search_messages(gmail_query, page_size=page_size)
                    summaries = list(getattr(page, "items", []) or [])
                    messages = await _load_gmail_details(service, summaries)
                    if not messages:
                        yield _sse("token", {"delta": "Không có email nào khớp trong hộp thư đến."})
                    else:
                        async for delta in summarize_emails_stream(
                            query, messages, timeout_seconds=45.0
                        ):
                            yield _sse("token", {"delta": delta})
                    yield _sse(
                        "citations",
                        {
                            "data": {"query": gmail_query, "count": len(messages)},
                        },
                    )
                    yield _sse("done", {"run_id": run_id, "status": "completed"})
                    return
            else:
                result = QueryResult(
                    run_id=run_id,
                    status="routed",
                    message="Yêu cầu đã được định tuyến nhưng chưa thực thi.",
                    route=route,
                )

            # Stream message
            if result.message:
                yield _sse("token", {"delta": result.message})

            # Stream citations/data/approval
            if result.data or result.approval_id:
                yield _sse(
                    "citations",
                    {
                        "data": result.data,
                        "approval_id": result.approval_id,
                    },
                )

            yield _sse("done", {"run_id": run_id, "status": result.status})

        except (
            AuthenticationError,
            ExternalServiceError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            final_status = RunStatus.COMPLETED
            blocked_msg = _blocked_message(exc)
            yield _sse("token", {"delta": blocked_msg})
            yield _sse("done", {"run_id": run_id, "status": "blocked", "error_code": exc.code})
        except Exception as exc:
            final_status = RunStatus.FAILED
            final_error = str(exc)
            yield _sse("error", {"message": "Đã xảy ra lỗi khi xử lý yêu cầu."})
            raise
        finally:
            try:
                await self._complete(
                    session, run_id, started, status=final_status, error_summary=final_error
                )
            except Exception:
                logger.warning("stream_complete_run_failed", exc_info=True)

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

    async def _handle_communication_draft(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        route: QueryRouteInfo,
    ) -> QueryResult:
        from app.domain.enums import ActionRiskLevel
        from app.domain.models import ProposedAction

        subject, body, recipients = await compose_email_draft(query)
        to_list = recipients or ["quanly@vov.vn"]
        proposal = ProposedAction(
            action_type="create_draft",
            description=f"Tạo bản nháp email '{subject}' tới {', '.join(to_list)} trên Gmail",
            tool_name="gmail.create_draft",
            parameters={
                "to": to_list,
                "subject": subject,
                "body_text": body,
            },
            risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
            requires_approval=True,
        )
        approval = await ApprovalRequestService.create_request(session, run_id, proposal)

        msg = (
            f"Tôi đã soạn sẵn nội dung email theo yêu cầu của bạn:\n\n"
            f"📌 **Tiêu đề:** {subject}\n\n"
            f"📬 **Người nhận:** {', '.join(to_list)}\n\n"
            f"---\n\n"
            f"{body}\n\n"
            f"---\n\n"
            f"Vui lòng kiểm tra nội dung ở trên. Bạn có thể nhấn **Duyệt** trên thẻ xác nhận bên dưới để tự động tạo bản nháp này trên Gmail của bạn."
        )
        return QueryResult(
            run_id=run_id,
            status="needs_approval",
            message=msg,
            route=route,
            data={
                "proposal": proposal.model_dump(mode="json"),
                "subject": subject,
                "body": body,
                "to": to_list,
            },
            approval_id=approval.id,
        )

    async def _handle_communication(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        route: QueryRouteInfo,
    ) -> QueryResult:
        if is_communication_draft(query):
            return await self._handle_communication_draft(session, user_id, query, run_id, route)

        if is_communication_mutation(query):
            return QueryResult(
                run_id=run_id,
                status="routed",
                message=(
                    "Gửi hoặc xóa email trực tiếp không được thực hiện trên POST /query vì lý do an toàn. "
                    "Bạn có thể yêu cầu: 'Soạn email gửi [người nhận] về [nội dung]' để tôi soạn thảo trước và tạo thẻ duyệt cho bạn."
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

            self._pipeline = build_retrieval_pipeline(use_viranker=False, per_document_cap=4)
        from app.domain.models.retrieval import RetrievalQuery

        retrieval_query = RetrievalQuery(
            original_query=query,
            search_query=query,
            requester_id=retrieval_requester_id(user_id),
            top_k_dense=25,
            top_k_sparse=25,
        )
        return await self._pipeline.run_with_synthesis(retrieval_query, internal_only=True)

    async def _run_retrieval_stream(
        self, query: str, user_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        if self._pipeline is None:
            from app.services.retrieval.factory import build_retrieval_pipeline

            self._pipeline = build_retrieval_pipeline(use_viranker=False, per_document_cap=4)
        from app.domain.models.retrieval import RetrievalQuery

        retrieval_query = RetrievalQuery(
            original_query=query,
            search_query=query,
            requester_id=retrieval_requester_id(user_id),
            top_k_dense=25,
            top_k_sparse=25,
        )
        async for event in self._pipeline.run_with_streaming_synthesis(
            retrieval_query, internal_only=True
        ):
            yield event

    @staticmethod
    async def _ensure_user(session: AsyncSession, user_id: str) -> None:
        if await session.get(User, user_id) is not None:
            return
        collision = await session.scalar(
            select(User.id).where(User.email == f"{user_id}@users.local")
        )
        email = (
            f"{user_id}@users.local"
            if collision is None
            else f"{user_id}-{uuid.uuid4().hex[:8]}@users.local"
        )
        session.add(User(id=user_id, email=email))
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
            session, run_id, status, latency_ms, error_summary=error_summary
        )


__all__ = [
    "GOOGLE_CONNECT_HINT",
    "QueryOrchestrator",
    "QueryResult",
    "QueryRouteInfo",
    "build_gmail_search_query",
    "calendar_mutation_kind",
    "compose_email_draft",
    "fallback_summarize_emails",
    "infer_calendar_window",
    "infer_event_summary",
    "infer_past_calendar_window",
    "is_communication_draft",
    "is_communication_mutation",
    "parse_event_times",
    "retrieval_requester_id",
    "route_info",
    "summarize_emails",
]
