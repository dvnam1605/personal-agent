"""Calendar, Gmail, workflow and document-generation handlers, and the shared dispatch.

Reads run against the live Google services; mutations (calendar events, email
drafts) become approval requests instead of hitting Google directly. The command chain
and the routed dispatch live here so the JSON path (``handle``) and the SSE path
(``handle_stream``) run exactly the same handlers in the same order.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.declarations import CALENDAR_AGENT_NAME, COMMUNICATION_AGENT_NAME
from app.agents.specialist.calendar import DEFAULT_TIMEZONE, build_event_proposal
from app.core.config import settings
from app.domain.enums import RouteType, is_supervisor_route, is_workflow_route
from app.domain.errors import AppError, ValidationError
from app.domain.models.routing.route import RouteDecision
from app.services.approvals import ApprovalRequestService

from .commands.routines import RoutineMixin
from .mail.drafting import ask_recipients, prepare_draft, review_note
from .mail.email_synthesis import compose_email_draft, summarize_emails
from .models import QueryResult, QueryRouteInfo, route_info
from .parsing import (
    build_gmail_search_query,
    calendar_mutation_kind,
    infer_calendar_window,
    infer_event_summary,
    is_communication_draft,
    is_communication_mutation,
    iso,
    load_gmail_details,
    parse_event_times,
    retrieval_requester_id,
    serialize_event,
)

# Records keep the orchestrator's logger name.
logger = logging.getLogger("app.services.routing.query.orchestrator")

# Short replies that approve a pending document outline ("duyệt", "ok viết đi").
# At most two trailing words, so a real question starting with "ok" is not approval.
_APPROVAL_RE = re.compile(
    r"^(?:duyệt|duyet|đồng ý|dong y|ok|oke|okay|viết đi|viet di|bắt đầu|bat dau|"
    r"tiến hành|tien hanh|làm đi|lam di)\b[\s!.,]*(?:\w+[\s!.,]*){0,2}$",
    re.IGNORECASE,
)

REJECT_MESSAGE = "Yêu cầu bị từ chối vì vi phạm ranh giới an toàn."
CLARIFY_MESSAGE = (
    "Bạn có thể nói rõ hơn được không? Ví dụ: xem lịch ngày mai, "
    "hỏi tài liệu nội bộ, hoặc tạo một cuộc họp."
)
CASUAL_MESSAGE = "Xin chào. Bạn cần tôi giúp gì?"
MAIL_MUTATION_MESSAGE = (
    "Gửi hoặc xóa email trực tiếp không được thực hiện trên POST /query vì lý do an toàn. "
    "Bạn có thể yêu cầu: 'Soạn email gửi [người nhận] về [nội dung]' để tôi soạn thảo "
    "trước và tạo thẻ duyệt cho bạn."
)


def fixed_reply(route_type: RouteType) -> tuple[str, str] | None:
    """The canned (status, message) for routes that need no specialist, else ``None``."""
    if route_type is RouteType.REJECT:
        return "rejected", REJECT_MESSAGE
    if route_type is RouteType.CLARIFICATION:
        return "clarification_needed", CLARIFY_MESSAGE
    if route_type is RouteType.CASUAL_RESPONSE:
        return "casual_response", CASUAL_MESSAGE
    return None


class SpecialistMixin(RoutineMixin):
    """Workflow, calendar, communication and document handlers of ``QueryOrchestrator``."""

    async def _handle_command(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        effective_query: str,
        run_id: str,
        decision: RouteDecision,
        conversation_id: str | None,
    ) -> QueryResult | None:
        """Run the first chat command the turn is; ``None`` for an ordinary routed request.

        Commands (memory, web, routine, analysis, minutes, goal, task, document) read the
        user's own words; only document generation also sees the follow-up rewritten from
        history. Both the JSON and the SSE path call this inside their error handling, so a
        provider that refuses (an API not enabled, a revoked grant) becomes a "blocked"
        answer, not a 500.
        """
        handlers = (
            lambda: self._maybe_handle_memory(session, user_id, query, run_id, decision),
            lambda: self._maybe_handle_web(query, run_id, decision),
            lambda: self._maybe_handle_routines(session, user_id, query, run_id, decision),
            lambda: self._maybe_handle_analysis(session, user_id, query, run_id, decision),
            lambda: self._maybe_handle_minutes(session, user_id, query, run_id, decision),
            lambda: self._maybe_handle_goals(session, user_id, query, run_id, decision),
            lambda: self._maybe_handle_tasks(session, user_id, query, run_id, decision),
            lambda: self._maybe_handle_document(
                session, user_id, query, effective_query, run_id, decision, conversation_id
            ),
        )
        for handler in handlers:
            result = await handler()
            if result is not None:
                return result
        return None

    async def _dispatch_routed(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        decision: RouteDecision,
        route: QueryRouteInfo,
    ) -> QueryResult:
        """Run a workflow, supervisor, calendar or mail route.

        Callers answer canned routes (``fixed_reply``) and knowledge questions themselves,
        since the SSE path streams those token by token.
        """
        if is_workflow_route(decision.route_type) or is_supervisor_route(decision.route_type):
            handled = await self._handle_workflow(session, user_id, query, run_id, decision, route)
            return handled or self._fallback_workflow_result(run_id, decision, route)
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

            personal = await self._personal_context(session, user_id, query)
            signature = await self._memory_for(session).signature(user_id)
            if settings.agentic.supervisor_mode == "agentic":
                agentic = await self._run_agentic(
                    session, user_id, query, run_id, route, personal, signature
                )
                if agentic is not None:
                    return agentic

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
                personal=personal,
                signature=signature,
                critic=self._draft_critic,
                compose=self._compose_email_fn or compose_email_draft,
            )
        return None

    async def _run_agentic(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        route: QueryRouteInfo,
        personal: str,
        signature: str | None,
    ) -> QueryResult | None:
        """Answer with the agentic supervisor; None means "use the rule pipeline".

        A failed run falls back only when ``fallback_to_rules`` is on; the bridge never
        fails after creating an approval, so a fallback cannot duplicate one.
        """
        from app.services.routing.agentic_bridge import AgenticRunError, run_agentic_supervisor

        try:
            return await run_agentic_supervisor(
                session=session,
                user_id=user_id,
                query=query,
                run_id=run_id,
                route=route,
                personal=personal,
                signature=signature,
                critic=self._draft_critic,
                builder=self._agentic_builder,
            )
        except (AgenticRunError, AppError) as exc:
            if not settings.agentic.fallback_to_rules:
                raise
            logger.warning("agentic_fallback", extra={"run_id": run_id, "error": str(exc)})
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
        events = [serialize_event(event) for event in list(getattr(page, "items", []) or [])]
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
                "window": {"start": iso(window_start), "end": iso(window_end)},
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
                start=iso(start),
                end=iso(end),
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
                "start": iso(start),
                "end": iso(end),
            },
            approval_id=approval.id,
        )

    def _documents(self) -> Any:
        if self._document_service is None:
            from app.services.documents.service import DocumentGenerationService

            self._document_service = DocumentGenerationService(
                metadata_service=self._metadata(), pipeline=self._live_pipeline()
            )
        return self._document_service

    async def _maybe_handle_document(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        effective_query: str,
        run_id: str,
        decision: RouteDecision,
        conversation_id: str | None,
    ) -> QueryResult | None:
        """Document generation in chat: request → outline; "duyệt" → written document.

        Returns ``None`` when the turn is not about generating a document, so
        normal routing continues. Rejected requests never reach this point.
        """
        if decision.route_type is RouteType.REJECT:
            return None
        try:
            # A savepoint, so a failed DB step leaves the request's transaction usable.
            async with session.begin_nested():
                row, message = await self._document_turn(
                    session, user_id, query, effective_query, conversation_id
                )
        except Exception:  # noqa: BLE001 - fall back to normal routing
            logger.exception("document_generation_route_failed", extra={"run_id": run_id})
            return None
        if row is None:
            return None
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=message,
            route=route_info(decision),
            data={
                "generated_document": {
                    "id": row.id,
                    "status": row.status,
                    "title": row.title,
                    "has_docx": bool(row.docx_path),
                    "file_format": Path(row.docx_path).suffix.lstrip(".")
                    if row.docx_path
                    else None,
                    "download_path": f"/documents/{row.id}/download" if row.docx_path else None,
                }
            },
        )

    async def _document_turn(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        effective_query: str,
        conversation_id: str | None,
    ) -> tuple[Any | None, str]:
        """Approve a pending outline or start a new one; ``(None, "")`` when neither applies."""
        from app.services.documents.service import (
            DocumentGenerationService,
            outline_message,
            result_message,
        )
        from app.services.documents.templates import is_generation_request

        pending = await DocumentGenerationService.pending_outline(session, user_id, conversation_id)
        requester = retrieval_requester_id(user_id)
        if pending is not None and _APPROVAL_RE.match(query.strip()):
            row = await self._documents().approve_and_write(
                session, user_id=user_id, requester_id=requester, doc_id=pending.id
            )
            return row, result_message(row)
        if is_generation_request(effective_query):
            row = await self._documents().create_outline(
                session,
                user_id=user_id,
                requester_id=requester,
                request=effective_query,
                conversation_id=conversation_id,
            )
            return row, outline_message(row)
        return None, ""

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

        personal = await self._personal_context(session, user_id, query)
        compose = self._compose_email_fn or compose_email_draft
        subject, body, recipients = await compose(query, personal=personal or None)
        plan = await prepare_draft(
            query,
            subject,
            body,
            recipients,
            personal=personal,
            signature=await self._memory_for(session).signature(user_id),
            critic=self._draft_critic,
        )
        if plan.needs_recipients:
            return ask_recipients(run_id, route, query, subject)
        subject, body = plan.draft.subject, plan.draft.body
        to_list = list(plan.draft.recipients)
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

        note = review_note(plan)
        msg = (
            f"Tôi đã soạn sẵn nội dung email theo yêu cầu của bạn:\n\n"
            f"📌 **Tiêu đề:** {subject}\n\n"
            f"📬 **Người nhận:** {', '.join(to_list)}\n\n"
            f"---\n\n"
            f"{body}\n\n"
            f"---\n\n"
            f"{note + chr(10) * 2 if note else ''}"
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
                "review": {"fixed": plan.fixed, "remaining": plan.remaining},
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
                message=MAIL_MUTATION_MESSAGE,
                route=route,
            )
        gmail_query, page_size = build_gmail_search_query(query)
        service = await self._communication(session, user_id)
        page = await service.search_messages(gmail_query, page_size=page_size)
        summaries = list(getattr(page, "items", []) or [])
        messages = await load_gmail_details(service, summaries)
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
