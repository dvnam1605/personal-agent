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
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
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

# With the reranker disabled RRF order is final, so keep only the strongest chunks.
_RAG_TOP_K_DENSE = 50
_RAG_TOP_K_SPARSE = 60
_RAG_MAX_CANDIDATES = 16
_RAG_PER_DOCUMENT_CAP = 4
_RAG_CONTEXT_TOKEN_BUDGET = 8192
# Short replies that approve a pending document outline ("duyệt", "ok viết đi").
_APPROVAL_RE = re.compile(
    # At most two trailing words, so a real question starting with "ok" is not approval.
    r"^(?:duyệt|duyet|đồng ý|dong y|ok|oke|okay|viết đi|viet di|bắt đầu|bat dau|"
    r"tiến hành|tien hanh|làm đi|lam di)\b[\s!.,]*(?:\w+[\s!.,]*){0,2}$",
    re.IGNORECASE,
)
# Profile-selected documents read together in one scoped RAG pass.
_RAG_MAX_SCOPED_DOCUMENTS = 8


# Per-request follow-up scope: (documents the previous answer cited, the line pointed at).
_FOLLOWUP_SCOPE: ContextVar[tuple[list[str], str] | None] = ContextVar(
    "_FOLLOWUP_SCOPE", default=None
)


def _mentions(text: str, target: str) -> bool:
    """Whether ``text`` names ``target`` (accent/punctuation-insensitive prefix)."""
    from app.core.text import unaccent_vietnamese

    def norm(s: str) -> str:
        return re.sub(r"[\W_]+", "", unaccent_vietnamese(s).lower())

    key = norm(target)[:24]
    return not key or key in norm(text)


@dataclass
class _KnowledgeRoute:
    """Either a finished structured answer or the RAG query to run."""

    answer: str | None = None
    citations: list[Any] = field(default_factory=list)
    status: Any = None
    rag_query: Any = None
    prefix: str = ""
    # Relation facts from SQL whose documents are read before answering.
    relation: Any = None


FOLLOWUP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*(còn|tiếp|thế còn|vậy còn|ngoài ra|chi tiết hơn|xem thêm|nữa không|hết chưa)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(còn\s+(?:văn\s+bản|tài\s+liệu|quyết\s+định|báo\s+cáo|hợp\s+đồng|biên\s+bản|file)\s+nào"
        r"|còn\s+gì\s+nữa\s+không|còn\s+nữa\s+không|tiếp\s+tục|tiếp\s+đi)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(ông\s+ấy|bà\s+ấy|anh\s+ấy|chị\s+ấy|người\s+đó"
        r"|(?:văn\s+bản|tài\s+liệu|quyết\s+định|báo\s+cáo|hợp\s+đồng|biên\s+bản|file)\s+(?:đó|này|trên))\b",
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


# Function words of a follow-up request: they say *how* to answer, not *what*.
_FOLLOWUP_STOPWORDS = frozenset(
    "trinh bay chi tiet cho toi minh em ban hay giai thich ro hon them noi ve cai nay "
    "do kia la gi the nao sao di nhe voi cua va cac nhung mot phan tich lam xem duoc "
    "khong can muon biet ky tom tat mo ta cu the hoi tiep nua o tren duoi".split()
)
_TOKEN = re.compile(r"\w+", re.UNICODE)


def _folded_tokens(text: str) -> list[str]:
    from app.core.text import unaccent_vietnamese

    return _TOKEN.findall(unaccent_vietnamese(text).lower())


def answer_anchor(query: str, history: list[dict[str, str]] | None) -> str | None:
    """The lines of the previous answer this turn points at, if any.

    Lines are scored by shared non-request word pairs; pairs with a number weigh more.
    """
    if not history:
        return None
    answer = next(
        (m.get("content", "") for m in reversed(history) if m.get("role") == "assistant"), ""
    )
    if not answer:
        return None
    tokens = _folded_tokens(query)
    pairs = [
        (a, b)
        for a, b in zip(tokens, tokens[1:], strict=False)
        if not (a in _FOLLOWUP_STOPWORDS and b in _FOLLOWUP_STOPWORDS)
    ]
    if not pairs:
        return None
    scored: list[tuple[int, int, str]] = []
    for idx, line in enumerate(answer.splitlines()):
        clean = line.strip()
        if not clean:
            continue
        words = _folded_tokens(clean)
        line_pairs = set(zip(words, words[1:], strict=False))
        score = sum(
            2 if (a.isdigit() or b.isdigit()) else 1 for a, b in pairs if (a, b) in line_pairs
        )
        if score:
            scored.append((score, -idx, clean))
    if not scored:
        return None
    best = max(scored)[0]
    lines = [
        text for score, _, text in sorted(scored, key=lambda t: t[1], reverse=True) if score == best
    ]
    excerpt = " ".join(re.sub(r"[*_#>`]|\[\d+\]", "", ln).strip(" -") for ln in lines[:2])
    return excerpt[:600] or None


_STANDALONE_PROMPT = (
    "Viết lại câu hỏi tiếp nối của người dùng thành MỘT câu hỏi độc lập, đầy đủ chủ thể, "
    "bằng tiếng Việt, để tìm trong kho tài liệu. Dùng câu hỏi trước và đoạn câu trả lời mà "
    "người dùng đang nhắc tới để nêu rõ họ hỏi về điều gì (tên khái niệm, tên phần, tên tài "
    "liệu). Giữ yêu cầu của người dùng (trình bày chi tiết, so sánh…). Chỉ trả về câu hỏi."
)


async def standalone_followup(query: str, history: list[dict[str, str]], anchor: str) -> str:
    """A self-contained question for a turn that points into the previous answer."""
    previous = next(
        (m.get("content", "").strip() for m in reversed(history) if m.get("role") == "user"), ""
    )
    fallback = f"{previous} — {anchor} ({query})" if previous else f"{anchor} ({query})"
    try:
        from app.services.documents.llm import llm_text

        rewritten = await llm_text(
            _STANDALONE_PROMPT,
            f"Câu hỏi trước: {previous}\nĐoạn được nhắc tới: {anchor}\nCâu hỏi tiếp nối: {query}",
            timeout=15.0,
            temperature=0.0,
        )
    except Exception:  # noqa: BLE001 - the deterministic condensation is fine
        rewritten = None
    rewritten = (rewritten or "").strip().strip('"')
    if not rewritten or len(rewritten) > 400 or "\n" in rewritten:
        return fallback
    return rewritten


def condense_query_with_history(
    query: str, history: list[dict[str, str]], *, force: bool = False
) -> str:
    """Condense a follow-up query with the prior conversation subject/entities.

    ``force`` condenses even when no follow-up phrase matched.
    """
    if not history or not (force or is_followup_query(query)):
        return query

    last_user_query = ""
    for msg in reversed(history):
        if msg.get("role") == "user":
            last_user_query = msg.get("content", "").strip()
            break

    if not last_user_query:
        return query

    # Substitute the person from the previous question for the pronoun, keeping the rest.
    person = _PERSON_MENTION.search(last_user_query)
    if person and _PERSON_PRONOUN.search(query):
        return _PERSON_PRONOUN.sub(person.group(0).strip(), query, count=1)
    # "còn … nào nữa không", "chi tiết hơn": same question, asking for more.
    return f"{last_user_query} ({query})"


# Honorific + capitalised name, e.g. "ông Nguyễn Văn An" (no names hard-coded).
_PERSON_MENTION = re.compile(
    r"\b(?:ông|bà|anh|chị|đồng chí|đ/c|thầy|cô)\s+"
    r"[A-ZĐÀ-Ỹ][\wÀ-ỹ]*(?:\s+[A-ZĐÀ-Ỹ][\wÀ-ỹ]*){0,4}"
)
_PERSON_PRONOUN = re.compile(r"\b(?:ông|bà|anh|chị|người)\s+(?:ấy|đó|này)\b", re.IGNORECASE)


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
        metadata_service: Any | None = None,
        relation_service: Any | None = None,
        phrase_structured: bool = True,
        pipeline: Any | None = None,
    ) -> None:
        self._metadata_service = metadata_service
        self._relation_service = relation_service
        # SQL answers are reworded by one short LLM call (facts verified).
        self._phrase_structured = phrase_structured
        self._document_service: Any | None = None
        self._triage = triage or FastTriage()
        self._calendar_for_user = calendar_for_user
        self._communication_for_user = communication_for_user
        self._retrieve = retrieve
        self._oauth_service = oauth_service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_run_id = new_run_id or (lambda: str(uuid.uuid4()))
        self._summarize_emails_fn = summarize_emails_fn
        self._pipeline: Any | None = pipeline

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
        effective_query, decision, anchor = await self._triage_in_context(query, history)
        await self._set_followup_scope(session, conversation_id, anchor)
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
        session: AsyncSession | None = None,
    ) -> QueryResult:
        synthesis = await self._run_retrieval(
            query, user_id, conversation_history=conversation_history
        )
        citations = [
            citation.model_dump(mode="json")
            for citation in list(getattr(synthesis, "citations", []))
        ]
        status = getattr(getattr(synthesis, "status", None), "value", None)
        await self._record_evidence(
            session,
            run_id,
            evidence=getattr(synthesis, "evidence", None),
            retrieval_trace_id=getattr(synthesis, "retrieval_trace_id", None),
            answer_status=status,
        )
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=str(getattr(synthesis, "answer", "")),
            route=route,
            data={"citations": citations, "sufficiency": status},
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
        from app.services.documents.service import (
            DocumentGenerationService,
            outline_message,
            result_message,
        )
        from app.services.documents.templates import is_generation_request

        if decision.route_type is RouteType.REJECT:
            return None
        try:
            pending = await DocumentGenerationService.pending_outline(
                session, user_id, conversation_id
            )
            requester = retrieval_requester_id(user_id)
            if pending is not None and _APPROVAL_RE.match(query.strip()):
                row = await self._documents().approve_and_write(
                    session, user_id=user_id, requester_id=requester, doc_id=pending.id
                )
                message = result_message(row)
            elif is_generation_request(effective_query):
                row = await self._documents().create_outline(
                    session,
                    user_id=user_id,
                    requester_id=requester,
                    request=effective_query,
                    conversation_id=conversation_id,
                )
                message = outline_message(row)
            else:
                return None
        except Exception:  # noqa: BLE001 - fall back to normal routing
            logger.exception("document_generation_route_failed", extra={"run_id": run_id})
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

    @staticmethod
    async def _record_evidence(
        session: AsyncSession | None,
        run_id: str,
        *,
        evidence: Any,
        retrieval_trace_id: str | None,
        answer_status: str | None,
    ) -> None:
        """Persist what the model saw for this run (best-effort, never fails the answer)."""
        if session is None:
            return
        try:
            from app.infrastructure.db.evidence_repository import record_run_evidence

            await record_run_evidence(
                session,
                run_id,
                evidence=list(evidence or []),
                retrieval_trace_id=retrieval_trace_id,
                answer_status=answer_status,
            )
        except Exception:  # noqa: BLE001 - audit trail must not break answering
            logger.warning("run_evidence_record_failed", extra={"run_id": run_id}, exc_info=True)

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

    @staticmethod
    async def _set_followup_scope(
        session: AsyncSession, conversation_id: str | None, anchor: str | None
    ) -> None:
        """Scope a follow-up's retrieval to what the previous answer cited."""
        _FOLLOWUP_SCOPE.set(None)
        if not anchor or not conversation_id:
            return
        try:
            from app.infrastructure.db.models import Message, RunEvidence

            message = (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id, Message.role == "assistant")
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if message is None:
                return
            data = (message.metadata_ or {}).get("data") or {}
            ids = [
                str(c.get("document_id"))
                for c in data.get("citations") or []
                if isinstance(c, dict) and c.get("document_id")
            ]
            if not ids and message.run_id:
                ids = list(
                    (
                        await session.execute(
                            select(RunEvidence.document_id).where(
                                RunEvidence.run_id == message.run_id, RunEvidence.cited
                            )
                        )
                    ).scalars()
                )
            ids = list(dict.fromkeys(i for i in ids if i))[:_RAG_MAX_SCOPED_DOCUMENTS]
            if ids:
                _FOLLOWUP_SCOPE.set((ids, anchor))
        except Exception:  # noqa: BLE001 - an unscoped follow-up still works
            logger.warning("followup_scope_failed", exc_info=True)

    async def _triage_in_context(
        self, query: str, history: list[dict[str, str]] | None
    ) -> tuple[str, RouteDecision, str | None]:
        """Route a turn with its conversation: resolve follow-ups, never ask twice.

        A turn pointing into the previous answer becomes a standalone question; other
        follow-ups and too-vague turns are condensed with the previous question.
        """
        effective_query = query
        anchor = answer_anchor(query, history) if history else None
        if history and anchor:
            effective_query = await standalone_followup(query, history, anchor)
            logger.info(
                "followup_resolved_from_answer",
                extra={"original": query, "standalone": effective_query},
            )
        elif history and is_followup_query(query):
            effective_query = condense_query_with_history(query, history)
            logger.info(
                "condensed_followup_query",
                extra={"original": query, "condensed": effective_query},
            )
        decision = self._triage.triage(effective_query)
        if decision.route_type is RouteType.CLARIFICATION and history and effective_query == query:
            condensed = condense_query_with_history(query, history, force=True)
            if condensed != query:
                retried = self._triage.triage(condensed)
                if retried.route_type is not RouteType.CLARIFICATION:
                    logger.info(
                        "clarification_resolved_from_history",
                        extra={"original": query, "condensed": condensed},
                    )
                    return condensed, retried, None
        return effective_query, decision, anchor

    def _live_pipeline(self) -> Any:
        if self._pipeline is None:
            from app.services.retrieval.factory import build_retrieval_pipeline

            self._pipeline = build_retrieval_pipeline(
                use_viranker=False,
                per_document_cap=_RAG_PER_DOCUMENT_CAP,
                rerank_top_k_max=_RAG_MAX_CANDIDATES,
            )
        return self._pipeline

    @staticmethod
    def _live_retrieval_query(query: str, user_id: str) -> Any:
        from app.domain.models.retrieval import RetrievalQuery

        return RetrievalQuery(
            original_query=query,
            search_query=query,
            requester_id=retrieval_requester_id(user_id),
            top_k_dense=_RAG_TOP_K_DENSE,
            top_k_sparse=_RAG_TOP_K_SPARSE,
            context_token_budget=_RAG_CONTEXT_TOKEN_BUDGET,
        )

    def _metadata(self) -> Any:
        if self._metadata_service is None:
            from app.services.retrieval.metadata import MetadataLookupService

            self._metadata_service = MetadataLookupService()
        return self._metadata_service

    def _relations(self) -> Any:
        if self._relation_service is None:
            from app.services.retrieval.relations import RelationLookupService

            self._relation_service = RelationLookupService()
        return self._relation_service

    async def _route_knowledge(self, query: str, user_id: str) -> _KnowledgeRoute:
        """Pick SQL-over-relations, SQL-over-profiles, profile-scoped RAG, or corpus RAG.

        Any failure in the structured routes degrades to plain corpus RAG.
        """
        from app.domain.models.retrieval import RetrievalMode
        from app.domain.models.retrieval.metadata_filter import KnowledgeIntent
        from app.domain.models.retrieval.sufficiency import SufficiencyStatus
        from app.services.retrieval.metadata import render_metadata_answer
        from app.services.retrieval.metadata.render import describe_filters, describe_noun

        base_query = self._live_retrieval_query(query, user_id)
        requester = retrieval_requester_id(user_id)
        scope = _FOLLOWUP_SCOPE.get()
        if scope is not None:
            # Search the cited documents with the previous answer's own words.
            document_ids, anchor = scope
            logger.info("knowledge_route_planned", extra={"intent": "followup_scope"})
            return _KnowledgeRoute(
                rag_query=base_query.model_copy(
                    update={
                        "mode": RetrievalMode.DOCUMENT_SEARCH,
                        "document_ids": document_ids,
                        "search_query": f"{anchor} {query}"[:600],
                        "require_source_diversity": len(document_ids) > 1,
                    }
                )
            )
        try:
            relation = await self._relations().answer(query, requester)
            if relation is not None:
                logger.info(
                    "knowledge_route_planned", extra={"intent": "relation", **relation.trace}
                )
                if 0 < relation.total <= _RAG_MAX_SCOPED_DOCUMENTS:
                    # Facts give the documents and who; their text supplies the details.
                    return _KnowledgeRoute(rag_query=base_query, relation=relation)
                return _KnowledgeRoute(
                    answer=await self._phrase(query, relation.answer),
                    citations=relation.citations,
                    status=SufficiencyStatus.SUFFICIENT,
                )
        except Exception:  # noqa: BLE001 - the relation route must never break answering
            logger.exception("knowledge_relation_route_failed")
        try:
            service = self._metadata()
            plan = await service.plan(query, requester)
            logger.info(
                "knowledge_route_planned",
                extra={"intent": plan.intent.value, "reason": plan.reason},
            )
            if plan.intent is KnowledgeIntent.METADATA:
                result = await service.run(plan.spec, requester)
                answer, citations = render_metadata_answer(plan.spec, result)
                answer = await self._phrase(query, answer)
                status = (
                    SufficiencyStatus.SUFFICIENT if result.total else SufficiencyStatus.INSUFFICIENT
                )
                return _KnowledgeRoute(answer=answer, citations=citations, status=status)
            if plan.intent is KnowledgeIntent.METADATA_THEN_CONTENT:
                ids, total = await service.resolve_document_ids(
                    plan.spec, requester, cap=_RAG_MAX_SCOPED_DOCUMENTS
                )
                if total == 0:
                    note = (
                        f"(Không có {describe_noun(plan.spec)} nào"
                        f"{describe_filters(plan.spec)}; tìm trên toàn bộ kho tài liệu.)\n\n"
                    )
                    return _KnowledgeRoute(rag_query=base_query, prefix=note)
                if total > _RAG_MAX_SCOPED_DOCUMENTS:
                    result = await service.run(plan.spec, requester)
                    answer, citations = render_metadata_answer(plan.spec, result)
                    answer += (
                        f"\n\nCó {total} tài liệu khớp, quá nhiều để đọc nội dung cùng lúc; "
                        "hãy nêu rõ hơn (số hiệu, thời gian, người liên quan)."
                    )
                    return _KnowledgeRoute(
                        answer=answer, citations=citations, status=SufficiencyStatus.PARTIAL
                    )
                scoped = base_query.model_copy(
                    update={
                        "mode": RetrievalMode.DOCUMENT_SEARCH,
                        "document_ids": ids,
                        "require_source_diversity": len(ids) > 1,
                    }
                )
                return _KnowledgeRoute(rag_query=scoped)
        except Exception:  # noqa: BLE001 - the metadata route must never break answering
            logger.exception("knowledge_metadata_route_failed")
        return _KnowledgeRoute(rag_query=base_query)

    async def _phrase(self, question: str, draft: str) -> str:
        """Natural wording for a SQL-backed answer (facts unchanged; draft on doubt)."""
        if not self._phrase_structured:
            return draft
        from app.services.retrieval.verbalize import phrase_structured_answer

        try:
            return await phrase_structured_answer(question, draft)
        except Exception:  # noqa: BLE001 - wording must never break answering
            logger.exception("structured_phrasing_failed")
            return draft

    async def _answer_relation(
        self,
        query: str,
        route: _KnowledgeRoute,
        conversation_history: list[dict[str, str]] | None,
    ) -> Any:
        """Relation facts from SQL + the text of those documents → one LLM answer.

        Facts go in the system prompt so grounding checks still judge the user's own
        question; omitted targets are appended, and failures fall back to the fact list.
        """
        from app.domain.models.retrieval import RetrievalMode
        from app.domain.models.retrieval.sufficiency import SufficiencyStatus
        from app.services.retrieval.prompts import SYNTHESIS_SYSTEM_PROMPT
        from app.services.retrieval.synthesis import SynthesisResult

        relation = route.relation
        # With signers plus another relation, the signers are what was asked.
        types = {str(f["relation_type"]) for f in relation.facts}
        asked = {"signed_by"} if "signed_by" in types and len(types) > 1 else types
        targets = list(
            dict.fromkeys(
                str(f["target_text"]) for f in relation.facts if f["relation_type"] in asked
            )
        )
        facts_text = re.sub(r"\s*\[\d+\]", "", relation.answer)
        scoped = route.rag_query.model_copy(
            update={
                "mode": RetrievalMode.DOCUMENT_SEARCH,
                "document_ids": [c.document_id for c in relation.citations],
                "require_source_diversity": len(relation.citations) > 1,
                "search_query": f"{query} {' '.join(targets)}"[:500],
            }
        )
        system_prompt = (
            f"{SYNTHESIS_SYSTEM_PROMPT}\n"
            "DỮ KIỆN ĐÃ TRA CHÍNH XÁC TỪ CƠ SỞ DỮ LIỆU (đầy đủ cho câu hỏi này):\n"
            f"{facts_text}\n\n"
            "Yêu cầu thêm:\n"
            "- Dữ kiện trên đã được xác minh từ văn bản gốc: NÊU TRỰC TIẾP kể cả khi đoạn "
            "bằng chứng E# không chứa thông tin đó (khi ấy không cần nhãn [E#] cho câu đó). "
            "Tuyệt đối không nói dữ kiện nào đó 'không có' hay 'chưa thể hiện'.\n"
            "- Nêu ĐỦ mọi văn bản và mọi đối tượng trong dữ kiện trên, không bỏ sót.\n"
            "- Trả lời đúng trọng tâm câu hỏi: chỉ thêm chi tiết từ bằng chứng E# (chức vụ, đơn "
            "vị, ngày hiệu lực, lý do…) khi nó trực tiếp trả lời câu hỏi, gắn trích dẫn [E#].\n"
            "- Văn phong tự nhiên như người trả lời đồng nghiệp: mở đầu bằng câu trả lời thẳng; "
            "1–3 văn bản viết thành đoạn văn ngắn, nhiều hơn thì danh sách gạch đầu dòng gọn, "
            "mỗi văn bản một dòng. Không dùng tiêu đề (#), không kẻ ngang (---), hạn chế in đậm; "
            "không mở đầu kiểu 'Dựa trên các tài liệu được cung cấp', 'Dưới đây là'.\n"
            "- Không nêu điều bằng chứng không có, không viết câu kiểu 'chưa có thông tin…'; "
            "không nhắc tới 'dữ kiện' hay 'cơ sở dữ liệu'."
        )
        try:
            result = await self._live_pipeline().run_with_synthesis(
                scoped,
                internal_only=True,
                conversation_history=conversation_history,
                system_prompt=system_prompt,
            )
        except Exception:  # noqa: BLE001 - fall back to the fact list
            logger.exception("relation_content_answer_failed")
            result = None
        if result is None or not result.citations:
            return SynthesisResult(
                answer=await self._phrase(query, relation.answer),
                citations=relation.citations,
                status=SufficiencyStatus.SUFFICIENT,
            )
        missing = [t for t in targets if not _mentions(result.answer, t)]
        if missing:
            logger.info("relation_answer_missing_targets count=%d", len(missing))
            result = result.model_copy(
                update={
                    "answer": result.answer.rstrip()
                    + "\n\nNgoài ra, theo các văn bản trên còn có: "
                    + "; ".join(missing)
                    + "."
                }
            )
        if result.status is not SufficiencyStatus.SUFFICIENT:
            # The facts themselves are exact; missing detail is not missing answer.
            result = result.model_copy(update={"status": SufficiencyStatus.SUFFICIENT})
        return result

    async def _run_retrieval(
        self,
        query: str,
        user_id: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> Any:
        if self._retrieve is not None:
            return await self._retrieve(query, user_id)
        from app.services.retrieval.synthesis import SynthesisResult

        route = await self._route_knowledge(query, user_id)
        if route.relation is not None:
            return await self._answer_relation(query, route, conversation_history)
        if route.answer is not None:
            return SynthesisResult(
                answer=route.answer, citations=route.citations, status=route.status
            )
        result = await self._live_pipeline().run_with_synthesis(
            route.rag_query,
            internal_only=True,
            conversation_history=conversation_history,
        )
        if route.prefix:
            result = result.model_copy(update={"answer": route.prefix + result.answer})
        return result

    async def _run_retrieval_stream(
        self,
        query: str,
        user_id: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        route = await self._route_knowledge(query, user_id)
        if route.relation is not None:
            # Not streamed: the answer is checked for missing facts before it is shown.
            result = await self._answer_relation(query, route, conversation_history)
            yield {"type": "token", "delta": result.answer}
            yield {
                "type": "citations",
                "citations": [c.model_dump(mode="json") for c in result.citations],
                "status": result.status.value,
            }
            return
        if route.answer is not None:
            yield {"type": "token", "delta": route.answer}
            yield {
                "type": "citations",
                "citations": [c.model_dump(mode="json") for c in route.citations],
                "status": route.status.value,
            }
            return
        if route.prefix:
            yield {"type": "token", "delta": route.prefix}
        async for event in self._live_pipeline().run_with_streaming_synthesis(
            route.rag_query,
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
