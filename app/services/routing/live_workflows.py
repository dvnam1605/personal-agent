"""Live HTTP implementations of meeting-prep and meeting follow-up."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ActionRiskLevel
from app.domain.models import ProposedAction
from app.services.approvals import ApprovalRequestService
from app.services.routing.query import (
    QueryResult,
    QueryRouteInfo,
    infer_calendar_window,
    infer_past_calendar_window,
)
from app.services.skills.matching import unaccent_vietnamese as _unaccent

ListEventsFn = Callable[..., Awaitable[Any]]
SearchMessagesFn = Callable[..., Awaitable[Any]]
RetrieveFn = Callable[[str], Awaitable[Any]]

logger = logging.getLogger(__name__)

_WORKFLOW_STOP = frozenset(
    {
        "chuan",
        "bi",
        "hop",
        "cuoc",
        "ngay",
        "mai",
        "hom",
        "nay",
        "tuan",
        "soan",
        "thu",
        "follow",
        "up",
        "followup",
        "email",
        "voi",
        "va",
        "the",
        "meeting",
        "prep",
        "brief",
    }
)


async def run_meeting_prep(
    *,
    session: AsyncSession,
    user_id: str,
    query: str,
    run_id: str,
    route: QueryRouteInfo,
    now: datetime,
    list_events: ListEventsFn,
    search_messages: SearchMessagesFn,
    retrieve: RetrieveFn,
) -> QueryResult:
    """Find the next matching meeting, pull mail + docs, return a briefing."""
    del session, user_id
    window_start, window_end = infer_calendar_window(query, now=now)
    page = await list_events(window_start, window_end)
    events = list(getattr(page, "items", []) or [])
    meeting = _pick_event(events, query, now=now, upcoming=True)
    if meeting is None:
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=(
                "Không tìm thấy cuộc họp phù hợp trong khoảng thời gian này để chuẩn bị hồ sơ."
            ),
            route=route,
            data={"window": {"start": window_start.isoformat(), "end": window_end.isoformat()}},
        )

    attendees = _attendee_emails(meeting)
    discussions: list[str] = []
    for email in attendees[:5]:
        mail_page = await search_messages(f"in:inbox from:{email}", page_size=3)
        for item in list(getattr(mail_page, "items", []) or [])[:3]:
            snippet = getattr(item, "snippet", "") or getattr(item, "subject", "") or ""
            discussions.append(f"{email}: {snippet}".strip())

    topics = [getattr(meeting, "summary", "") or query]
    synthesis = await retrieve(topics[0] or query)
    answer = str(getattr(synthesis, "answer", "") or "")
    docs = [
        citation.model_dump(mode="json")
        for citation in list(getattr(synthesis, "citations", []) or [])
    ]
    title = getattr(meeting, "summary", "") or "(không tiêu đề)"
    when = _event_when(meeting)
    lines = [
        f"Hồ sơ họp: {title}",
        f"Thời gian: {when}" if when else None,
        f"Khách mời: {', '.join(attendees) if attendees else 'không có'}",
    ]
    if discussions:
        lines.append("Email gần đây:")
        lines.extend(f"- {item}" for item in discussions[:8])
    if answer:
        lines.append("Tài liệu nội bộ:")
        lines.append(answer[:800])
    message = "\n".join(item for item in lines if item)
    return QueryResult(
        run_id=run_id,
        status="completed",
        message=message,
        route=route,
        data={
            "meeting": {
                "id": getattr(meeting, "id", ""),
                "summary": title,
                "when": when,
                "attendees": attendees,
            },
            "discussions": discussions,
            "documents": docs,
        },
    )


async def run_meeting_followup(
    *,
    session: AsyncSession,
    query: str,
    run_id: str,
    route: QueryRouteInfo,
    now: datetime,
    list_events: ListEventsFn,
    search_messages: SearchMessagesFn,
) -> QueryResult:
    """Draft a follow-up email for the most recent matching meeting; wait for approval."""
    window_start, window_end = infer_past_calendar_window(query, now=now)
    page = await list_events(window_start, window_end)
    events = list(getattr(page, "items", []) or [])
    meeting = _pick_event(events, query, now=now, upcoming=False)
    if meeting is None:
        return QueryResult(
            run_id=run_id,
            status="clarification_needed",
            message=(
                "Không tìm thấy cuộc họp gần đây để soạn follow-up. "
                "Nói rõ tiêu đề họp hoặc khoảng thời gian."
            ),
            route=route,
        )
    attendees = _attendee_emails(meeting)
    if not attendees:
        return QueryResult(
            run_id=run_id,
            status="clarification_needed",
            message="Cuộc họp không có khách mời để gửi follow-up.",
            route=route,
            data={"meeting_id": getattr(meeting, "id", "")},
        )

    discussions: list[str] = []
    for email in attendees[:5]:
        mail_page = await search_messages(f"in:inbox from:{email}", page_size=2)
        for item in list(getattr(mail_page, "items", []) or [])[:2]:
            snippet = getattr(item, "snippet", "") or ""
            if snippet:
                discussions.append(snippet)

    title = getattr(meeting, "summary", "") or "cuộc họp"
    when = _event_when(meeting)
    body_lines = [
        "Xin chào,",
        "",
        f"Follow-up sau cuộc họp '{title}'" + (f" ({when})." if when else "."),
        "",
    ]
    if discussions:
        body_lines.append("Các trao đổi gần đây:")
        body_lines.extend(f"- {item}" for item in discussions[:5])
        body_lines.append("")
    body_lines.extend(
        [
            "Mong nhận được xác nhận các điểm còn mở và bước tiếp theo.",
            "",
            "Trân trọng.",
        ]
    )
    body = "\n".join(body_lines)
    subject = f"Follow-up: {title}"
    proposal = ProposedAction(
        action_type="create_draft",
        description=f"Tạo nháp follow-up '{subject}' tới {', '.join(attendees)}.",
        tool_name="gmail.create_draft",
        parameters={"to": attendees, "subject": subject, "body_text": body},
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        requires_approval=True,
    )
    approval = await ApprovalRequestService.create_request(session, run_id, proposal)
    return QueryResult(
        run_id=run_id,
        status="needs_approval",
        message=(
            f"{proposal.description} Duyệt và tạo nháp: "
            f'POST /approvals/{approval.id}/approve với {{"execute": true}}.'
        ),
        route=route,
        data={
            "proposal": proposal.model_dump(mode="json"),
            "meeting_id": getattr(meeting, "id", ""),
            "to": attendees,
            "subject": subject,
        },
        approval_id=approval.id,
    )


def _pick_event(
    events: list[Any],
    query: str,
    *,
    now: datetime,
    upcoming: bool,
) -> Any | None:
    if not events:
        return None
    keywords = {
        token
        for token in _unaccent(query).split()
        if token not in _WORKFLOW_STOP and len(token) > 1
    }

    def score(event: Any) -> tuple[int, datetime]:
        title = _unaccent(getattr(event, "summary", "") or "")
        hits = sum(1 for token in keywords if token in title)
        start = _event_start(event) or now
        return hits, start

    pool: list[Any] = []
    for event in events:
        start = _event_start(event)
        if start is None:
            continue
        if upcoming and start >= now:
            pool.append(event)
        elif not upcoming and start <= now:
            pool.append(event)
    if not pool:
        return None

    def rank_key(event: Any) -> tuple[int, float]:
        hits, start = score(event)
        offset = start.timestamp()
        return (-hits, offset if upcoming else -offset)

    return min(pool, key=rank_key)


def _attendee_emails(event: Any) -> list[str]:
    emails: list[str] = []
    for attendee in list(getattr(event, "attendees", []) or []):
        if getattr(attendee, "self_attendee", False) or getattr(attendee, "organizer", False):
            continue
        email = getattr(attendee, "email", "") or ""
        if email and email not in emails:
            emails.append(email)
    return emails


def _event_start(event: Any) -> datetime | None:
    start = getattr(event, "start", None)
    as_dt = getattr(start, "as_datetime", None)
    if not callable(as_dt):
        return None
    try:
        value = as_dt()
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, datetime) else None


def _event_when(event: Any) -> str:
    start = getattr(event, "start", None)
    to_google = getattr(start, "to_google", None)
    if callable(to_google):
        payload = to_google()
        if isinstance(payload, dict):
            val = payload.get("dateTime") or payload.get("date")
            if val:
                return str(val)
    if hasattr(start, "value"):
        return str(start.value)
    return str(start or "")


def _extract_research_topic(query: str) -> str:
    lower = query.lower()
    for conj in [" và xem", " và kiểm tra", " để soạn", " rồi ", " sau đó ", " đồng thời "]:
        if conj in lower:
            idx = lower.index(conj)
            prefix = query[:idx].strip()
            for verb in ["tìm kiếm ", "tìm ", "tra cứu ", "xem ", "đọc "]:
                if prefix.lower().startswith(verb):
                    prefix = prefix[len(verb):].strip()
            if prefix:
                return prefix
    return query


async def run_supervisor_dag(
    *,
    session: AsyncSession,
    user_id: str,
    query: str,
    run_id: str,
    route: QueryRouteInfo,
    now: datetime,
    list_events: ListEventsFn,
    search_messages: SearchMessagesFn,
    retrieve: RetrieveFn,
) -> QueryResult:
    """Execute a dynamic multi-domain supervisor DAG across Knowledge, Calendar, and Communication."""
    del search_messages
    query_unaccented = _unaccent(query).lower()

    has_research = any(
        d in route.domains for d in ("knowledge_research", "internal_doc")
    ) or any(
        k in query_unaccented
        for k in ("quy che", "chi tieu", "tai lieu", "quy dinh", "chinh sach")
    )

    has_calendar = "calendar" in route.domains or any(
        k in query_unaccented for k in ("lich", "hop", "su kien", "ngay mai", "tuan nay")
    )

    has_draft = (
        "communication" in route.domains
        and any(
            k in query_unaccented for k in ("soan", "email", "thu", "gui", "bao cao", "draft")
        )
    ) or ("soan" in query_unaccented and "email" in query_unaccented)

    # Step 1: Tra cứu kho tri thức (Knowledge Research)
    doc_answer = ""
    citations: list[dict[str, Any]] = []
    if has_research:
        topic = _extract_research_topic(query)
        try:
            synthesis = await retrieve(topic)
            doc_answer = str(getattr(synthesis, "answer", "") or "").strip()
            citations = [
                c.model_dump(mode="json")
                for c in list(getattr(synthesis, "citations", []) or [])
            ]
        except Exception:
            logger.exception("supervisor_dag_knowledge_retrieval_failed")
            doc_answer = "Không thể truy xuất tài liệu từ kho tri thức vào lúc này."

    # Step 2: Kiểm tra lịch (Google Calendar)
    cal_events: list[dict[str, Any]] = []
    cal_lines: list[str] = []
    if has_calendar:
        window_start, window_end = infer_calendar_window(query, now=now)
        try:
            page = await list_events(window_start, window_end)
            raw_items = list(getattr(page, "items", []) or [])
            for item in raw_items:
                summary = getattr(item, "summary", "") or "(Không có tiêu đề)"
                when = _event_when(item)
                cal_events.append({"summary": summary, "when": when})
                cal_lines.append(f"- {when}: {summary}" if when else f"- {summary}")
        except Exception:
            logger.exception("supervisor_dag_calendar_failed")
            cal_lines.append("(Không thể kết nối hoặc đọc Lịch Google)")

        if not cal_lines:
            cal_lines = ["(Không có sự kiện nào được lên lịch trong khoảng thời gian này)"]

    # Step 3: Soạn thảo email (Communication / Email Draft) hoặc Tổng hợp
    if has_draft:
        context_parts = []
        if doc_answer:
            context_parts.append(f"Quy chế / Tài liệu tra cứu được:\n{doc_answer}")
        if cal_lines:
            context_parts.append("Lịch làm việc / sự kiện liên quan:\n" + "\n".join(cal_lines))
        combined_context = "\n\n".join(context_parts)

        from app.services.routing.query.email_synthesis import compose_email_draft

        subject, body, recipients = await compose_email_draft(query, context=combined_context)
        to_list = recipients or ["ketoan@vov.vn", "quanly@vov.vn"]

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

        msg_lines = [
            "### ⚡ Kế hoạch đa tác vụ đã thực thi (Supervisor DAG)",
            "",
        ]
        if doc_answer:
            msg_lines.extend([
                "#### 1. 📚 Tra cứu kho tri thức",
                doc_answer,
                "",
            ])
        if cal_lines:
            msg_lines.extend([
                "#### 2. 📅 Thông tin lịch làm việc",
                "\n".join(cal_lines),
                "",
            ])
        msg_lines.extend([
            "#### 3. ✉️ Bản nháp email báo cáo đã soạn",
            f"📌 **Tiêu đề:** {subject}  ",
            f"📬 **Người nhận:** {', '.join(to_list)}  ",
            "",
            "---",
            "",
            body,
            "",
            "---",
            "",
            "> 💡 *Tôi đã tạo sẵn thẻ xác nhận bên dưới. Bạn có thể nhấn **Duyệt** để tự động tạo bản nháp này trên Gmail của bạn.*",
        ])
        return QueryResult(
            run_id=run_id,
            status="needs_approval",
            message="\n".join(msg_lines),
            route=route,
            data={
                "proposal": proposal.model_dump(mode="json"),
                "subject": subject,
                "body": body,
                "to": to_list,
                "citations": citations,
                "events": cal_events,
            },
            approval_id=approval.id,
        )

    # Non-draft multi-domain synthesis
    msg_lines = [
        "### ⚡ Kế hoạch đa tác vụ đã thực thi (Supervisor DAG)",
        "",
    ]
    if doc_answer:
        msg_lines.extend([
            "#### 1. 📚 Kết quả tra cứu kho tri thức",
            doc_answer,
            "",
        ])
    if cal_lines:
        msg_lines.extend([
            "#### 2. 📅 Thông tin lịch làm việc",
            "\n".join(cal_lines),
            "",
        ])

    return QueryResult(
        run_id=run_id,
        status="completed",
        message="\n".join(msg_lines),
        route=route,
        data={
            "citations": citations,
            "events": cal_events,
        },
    )


__all__ = ["run_meeting_followup", "run_meeting_prep", "run_supervisor_dag"]
