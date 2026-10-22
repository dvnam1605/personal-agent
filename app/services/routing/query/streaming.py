"""Server-sent-event streaming of a query's answer.

The stream mirrors ``QueryOrchestrator.handle``: the run is persisted, the routed
specialist runs, and tokens, citations and a final ``done`` event (with timing
metrics) are emitted as SSE frames before the run status is finalised.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncGenerator
from typing import Any

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
from app.services.platform.run_persistence import RunPersistenceService
from app.services.retrieval.answer.numbering import renumber

from .email_synthesis import summarize_emails_stream
from .models import QueryResult, route_info
from .parsing import (
    _blocked_message,
    _load_gmail_details,
    build_gmail_search_query,
    is_communication_draft,
    is_communication_mutation,
)

# Records keep the orchestrator's logger name.
logger = logging.getLogger("app.services.routing.query.orchestrator")


def _final_answer(streamed: str, placed: str | None) -> dict[str, str]:
    """The answer the client should keep when it differs from what streamed.

    Images placed after streaming and the answer's own figure/equation
    numbers ("Hình 1", "(1)") only exist once the whole answer is known.
    """
    final = renumber(placed or streamed)
    return {"answer": final} if final != streamed else {}


class StreamMixin:
    """The SSE path (``handle_stream``) of ``QueryOrchestrator``."""

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
        effective_query, decision, anchor = await self._triage_in_context(query, history)
        await self._set_followup_scope(session, conversation_id, anchor)
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
            # Approximate tokens as whitespace-separated chunks.
            tok_count = max(1, len(re.findall(r"\S+", delta)))
            total_tokens += tok_count
            yield _sse("token", {"delta": delta})

        async def _stream_text(text: str) -> AsyncGenerator[str, None]:
            if not text:
                return
            chunks = re.findall(r"\S+|\s+", text)
            if not chunks:
                return
            # Fast pacing for long answers, typewriter pacing for short ones.
            delay = min(0.018, max(0.004, 3.2 / max(1, len(chunks))))
            for chunk in chunks:
                async for sse_event in _yield_token(chunk):
                    yield sse_event
                if delay > 0:
                    await asyncio.sleep(delay)

        def _emit_done(status: str, extra: dict[str, Any] | None = None) -> str:
            now = time.perf_counter()
            ttft = (
                (first_token_at - stream_started_at)
                if first_token_at is not None
                else (now - stream_started_at)
            )
            stream_duration = (
                (last_token_at - first_token_at)
                if (
                    first_token_at is not None
                    and last_token_at is not None
                    and last_token_at > first_token_at
                )
                else max(0.001, now - stream_started_at)
            )
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
            if decision.route_type is not RouteType.REJECT:
                document_result = await self._maybe_handle_document(
                    session, user_id, query, effective_query, run_id, decision, conversation_id
                )
                if document_result is not None:
                    async for item in _stream_text(document_result.message):
                        yield item
                    yield _sse("document", document_result.data.get("generated_document", {}))
                    yield _emit_done("completed")
                    return

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
                streamed: list[str] = []
                async for event in self._run_retrieval_stream(
                    effective_query, user_id, conversation_history=history
                ):
                    ev_type = event.get("type")
                    if ev_type == "token":
                        delta = event.get("delta", "")
                        if delta:
                            streamed.append(delta)
                            async for item in _yield_token(delta):
                                yield item
                    elif ev_type == "citations":
                        # The prompt-text snapshot is stored server-side, not streamed.
                        await self._record_evidence(
                            session,
                            run_id,
                            evidence=event.get("evidence"),
                            retrieval_trace_id=event.get("retrieval_trace_id"),
                            answer_status=event.get("status"),
                        )
                        yield _sse(
                            "citations",
                            {
                                "citations": event.get("citations", []),
                                "sufficiency": event.get("status"),
                                "unsupported_claims": event.get("unsupported_claims", []),
                                **_final_answer("".join(streamed), event.get("answer")),
                            },
                        )
                yield _emit_done("completed")
                return

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
                        async for item in _stream_text(
                            "Không có email nào khớp trong hộp thư đến."
                        ):
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

            if result.message:
                async for item in _stream_text(result.message):
                    yield item

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
