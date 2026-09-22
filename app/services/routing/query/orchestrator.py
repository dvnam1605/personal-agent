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

FOLLOWUP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*(còn|tiếp|thế còn|vậy còn|ngoài ra|chi tiết hơn|xem thêm|nữa không|hết chưa)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(còn\s+văn\s+bản\s+nào|còn\s+quyết\s+định\s+nào|còn\s+gì\s+nữa\s+không|còn\s+nữa\s+không|tiếp\s+tục|tiếp\s+đi)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(ông\s+ấy|bà\s+ấy|văn\s+bản\s+đó|quyết\s+định\s+đó|người\s+đó)\b",
        re.IGNORECASE,
    ),
]


def is_followup_query(query: str) -> bool:
    """Detect if a user turn is a conversational follow-up depending on prior context."""
    q = query.strip().lower()
    for pattern in FOLLOWUP_PATTERNS:
        if pattern.search(q):
            return True
    words = q.split()
    if len(words) <= 5 and ("còn" in words or "nữa" in words or "tiếp" in words or "hết" in words):
        return True
    return False


def condense_query_with_history(query: str, history: list[dict[str, str]]) -> str:
    """Condense a follow-up query with the prior conversation subject/entities."""
    if not history or not is_followup_query(query):
        return query

    last_user_query = ""
    for msg in reversed(history):
        if msg.get("role") == "user":
            last_user_query = msg.get("content", "").strip()
            break

    if not last_user_query:
        return query

    lower_last = last_user_query.lower()
    subject = ""
    for name in [
        "đỗ tiến sỹ",
        "đỗ tiến sĩ",
        "vũ hải quang",
        "ngô minh hiển",
        "phạm mạnh hùng",
        "trần minh hùng",
    ]:
        if name in lower_last:
            subject = f"ông {name.title()}"
            break

    if not subject:
        m = re.search(
            r"\b(?:ông|bà|đồng chí|đ/c)\s+([A-ZÀ-Ỹa-zà-ỹ\s]+?)(?=\s+(?:đã|ký|kí|ban hành|\?|$))",
            last_user_query,
            re.IGNORECASE,
        )
        if m:
            subject = m.group(0).strip()

    if subject:
        return f"các văn bản quyết định khác do {subject} ký còn lại trong hệ thống"

    return f"{last_user_query} ({query})"


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

    @staticmethod
    async def _load_conversation_history(
        session: AsyncSession,
        conversation_id: str | None,
        user_id: str,
        limit: int = 6,
    ) -> list[dict[str, str]]:
        if not conversation_id:
            return []
        try:
            from app.infrastructure.db.models import Message

            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            res = await session.execute(stmt)
            msgs = list(reversed(res.scalars().all()))
            return [{"role": m.role, "content": m.content} for m in msgs]
        except Exception:
            logger.warning("load_conversation_history_failed", exc_info=True)
            return []

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
        effective_query = query
        if history and is_followup_query(query):
            effective_query = condense_query_with_history(query, history)
            logger.info(
                "condensed_followup_query",
                extra={"original": query, "condensed": effective_query},
            )

        decision = self._triage.triage(effective_query)
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

    async def handle_stream(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        *,
        correlation_id: str | None = None,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Triage a query, persist run, stream tokens via SSE, and finalize run status."""
        import json

        started = time.perf_counter()
        history = await self._load_conversation_history(session, conversation_id, user_id)
        effective_query = query
        if history and is_followup_query(query):
            effective_query = condense_query_with_history(query, history)
            logger.info(
                "condensed_followup_query",
                extra={"original": query, "condensed": effective_query},
            )

        decision = self._triage.triage(effective_query)
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

        stream_started_at = time.perf_counter()
        first_token_at: float | None = None
        last_token_at: float | None = None
        total_tokens: int = 0

        def _sse(event: str, data: dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        async def _yield_token(delta: str) -> AsyncGenerator[str, None]:
            nonlocal first_token_at, last_token_at, total_tokens
            if not delta:
                return
            now = time.perf_counter()
            if first_token_at is None:
                first_token_at = now
            last_token_at = now
            # Vietnamese / English token approximation: count non-whitespace word chunks
            tok_count = max(1, len(re.findall(r"\S+", delta)))
            total_tokens += tok_count
            yield _sse("token", {"delta": delta})

        async def _stream_text(text: str) -> AsyncGenerator[str, None]:
            if not text:
                return
            # Split into chunks of word + trailing whitespace or standalone whitespace:
            chunks = re.findall(r"\S+|\s+", text)
            if not chunks:
                return
            # Adaptive delay: fast for long answers, comfortable typewriter pacing for short answers
            delay = min(0.018, max(0.004, 3.2 / max(1, len(chunks))))
            for chunk in chunks:
                async for sse_event in _yield_token(chunk):
                    yield sse_event
                if delay > 0:
                    await asyncio.sleep(delay)

        def _emit_done(status: str, extra: dict[str, Any] | None = None) -> str:
            now = time.perf_counter()
            ttft = (first_token_at - stream_started_at) if first_token_at is not None else (now - stream_started_at)
            stream_duration = (last_token_at - first_token_at) if (first_token_at is not None and last_token_at is not None and last_token_at > first_token_at) else max(0.001, now - stream_started_at)
            tok_per_sec = (total_tokens / max(0.001, stream_duration)) if total_tokens > 0 else 0.0

            route_name = getattr(route, "route_type", None)
            if hasattr(route_name, "value"):
                route_name = route_name.value

            logger.info(
                "streaming_token_metrics",
                extra={
                    "run_id": run_id,
                    "route": str(route_name),
                    "ttft_seconds": round(ttft, 3),
                    "total_tokens": total_tokens,
                    "stream_duration_seconds": round(stream_duration, 3),
                    "tok_per_sec": round(tok_per_sec, 1),
                },
            )
            logger.info(
                "⚡ [STREAM PERF] run_id=%s | Route: %s | TTFT: %.3fs | Tokens: %d | Stream Time: %.2fs | Speed: %.1f tok/s",
                run_id,
                str(route_name),
                ttft,
                total_tokens,
                stream_duration,
                tok_per_sec,
            )

            done_payload: dict[str, Any] = {
                "run_id": run_id,
                "status": status,
                "ttft": round(ttft, 3),
                "token_count": total_tokens,
                "duration": round(stream_duration, 3),
                "tok_per_sec": round(tok_per_sec, 1),
            }
            if extra:
                done_payload.update(extra)
            return _sse("done", done_payload)

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
                async for item in _stream_text(msg):
                    yield item
                yield _emit_done("rejected")
                return

            if decision.route_type is RouteType.CLARIFICATION:
                msg = (
                    "Bạn có thể nói rõ hơn được không? Ví dụ: xem lịch ngày mai, "
                    "hỏi tài liệu nội bộ, hoặc tạo một cuộc họp."
                )
                async for item in _stream_text(msg):
                    yield item
                yield _emit_done("clarification_needed")
                return

            if decision.route_type is RouteType.CASUAL_RESPONSE:
                msg = "Xin chào. Bạn cần tôi giúp gì?"
                async for item in _stream_text(msg):
                    yield item
                yield _emit_done("casual_response")
                return

            agent = decision.target_agent
            if agent == KNOWLEDGE_RESEARCH_AGENT_NAME:
                # Real LLM token streaming from RAG pipeline with multi-turn conversation history
                async for event in self._run_retrieval_stream(
                    effective_query, user_id, conversation_history=history
                ):
                    ev_type = event.get("type")
                    if ev_type == "token":
                        delta = event.get("delta", "")
                        if delta:
                            async for item in _yield_token(delta):
                                yield item
                    elif ev_type == "citations":
                        yield _sse(
                            "citations",
                            {
                                "citations": event.get("citations", []),
                                "sufficiency": event.get("status"),
                            },
                        )
                yield _emit_done("completed")
                return

            # For other routes (Calendar, Email, Workflows), execute specialist
            result: QueryResult
            if is_workflow_route(decision.route_type) or is_supervisor_route(decision.route_type):
                handled = await self._handle_workflow(
                    session, user_id, query, run_id, decision, route
                )
                result = handled or self._fallback_workflow_result(run_id, decision, route)
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
                        async for item in _stream_text("Không có email nào khớp trong hộp thư đến."):
                            yield item
                    else:
                        async for delta in summarize_emails_stream(
                            query, messages, timeout_seconds=45.0
                        ):
                            async for item in _yield_token(delta):
                                yield item
                    yield _sse(
                        "citations",
                        {
                            "data": {"query": gmail_query, "count": len(messages)},
                        },
                    )
                    yield _emit_done("completed")
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
                async for item in _stream_text(result.message):
                    yield item

            # Stream citations/data/approval
            if result.data or result.approval_id:
                yield _sse(
                    "citations",
                    {
                        "data": result.data,
                        "approval_id": result.approval_id,
                    },
                )

            if result.status == "needs_approval":
                final_status = RunStatus.WAITING_APPROVAL
            else:
                final_status = RunStatus.COMPLETED

            yield _emit_done(result.status)

        except (
            AuthenticationError,
            ExternalServiceError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            final_status = RunStatus.COMPLETED
            blocked_msg = _blocked_message(exc)
            async for item in _stream_text(blocked_msg):
                yield item
            yield _emit_done("blocked", {"error_code": exc.code})
        except Exception as exc:
            final_status = RunStatus.FAILED
            final_error = str(exc)
            yield _sse("error", {"message": "Đã xảy ra lỗi khi xử lý yêu cầu."})
            raise
        finally:
            try:
                if final_status != RunStatus.WAITING_APPROVAL:
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
                user_id, query, run_id, route, conversation_history=conversation_history
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
        if is_supervisor_route(decision.route_type) or len(route.domains) > 1:
            from app.services.routing.live_workflows import run_supervisor_dag

            async def retrieve(topic: str) -> Any:
                return await self._run_retrieval(topic, user_id)

            return await run_supervisor_dag(
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
        return None

    def _fallback_workflow_result(
        self, run_id: str, decision: RouteDecision, route: QueryRouteInfo
    ) -> QueryResult:
        if is_supervisor_route(decision.route_type) or len(route.domains) > 1:
            domain_labels = {
                "calendar": "Lịch Google",
                "communication": "Email",
                "knowledge_research": "Kho tri thức",
                "internal_doc": "Tài liệu nội bộ",
            }
            detected = [domain_labels.get(d, d) for d in route.domains]
            detected_str = " + ".join(detected) if detected else "đa tác vụ"
            message = (
                f"Hệ thống đã nhận diện yêu cầu đa tác vụ liên quan đến: {detected_str}.\n\n"
                "Hiện tại trên luồng hội thoại trực tiếp, bạn nên thực hiện tuần tự các bước để đạt hiệu quả cao nhất:\n"
                "1. Tra cứu thông tin (ví dụ: 'Tìm quy chế chi tiêu công tác trong kho tài liệu')\n"
                "2. Tra cứu lịch (ví dụ: 'Xem lịch tuần này của tôi')\n"
                "3. Soạn thảo email (ví dụ: 'Soạn email báo cáo chi tiêu gửi phòng kế toán')"
            )
            return QueryResult(
                run_id=run_id,
                status="routed",
                message=message,
                route=route,
            )
        target = decision.target_workflow_id or decision.route_type.value
        return QueryResult(
            run_id=run_id,
            status="routed",
            message=(
                f"Yêu cầu được định tuyến tới {target}, nhưng quy trình này chưa được kích hoạt trực tiếp trên luồng hội thoại này."
            ),
            route=route,
        )

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
        conversation_history: list[dict[str, str]] | None = None,
    ) -> QueryResult:
        synthesis = await self._run_retrieval(
            query, user_id, conversation_history=conversation_history
        )
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

    async def _run_retrieval(
        self,
        query: str,
        user_id: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> Any:
        if self._retrieve is not None:
            return await self._retrieve(query, user_id)
        if self._pipeline is None:
            from app.services.retrieval.factory import build_retrieval_pipeline

            self._pipeline = build_retrieval_pipeline(use_viranker=False, per_document_cap=2)
        from app.domain.models.retrieval import RetrievalQuery

        retrieval_query = RetrievalQuery(
            original_query=query,
            search_query=query,
            requester_id=retrieval_requester_id(user_id),
            top_k_dense=50,
            top_k_sparse=60,
            context_token_budget=16384,
        )
        return await self._pipeline.run_with_synthesis(
            retrieval_query,
            internal_only=True,
            conversation_history=conversation_history,
        )

    async def _run_retrieval_stream(
        self,
        query: str,
        user_id: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        if self._pipeline is None:
            from app.services.retrieval.factory import build_retrieval_pipeline

            self._pipeline = build_retrieval_pipeline(use_viranker=False, per_document_cap=2)
        from app.domain.models.retrieval import RetrievalQuery

        retrieval_query = RetrievalQuery(
            original_query=query,
            search_query=query,
            requester_id=retrieval_requester_id(user_id),
            top_k_dense=50,
            top_k_sparse=60,
            context_token_budget=16384,
        )
        async for event in self._pipeline.run_with_streaming_synthesis(
            retrieval_query,
            internal_only=True,
            conversation_history=conversation_history,
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
