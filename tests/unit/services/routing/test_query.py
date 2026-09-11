"""Unit tests for `/query` window parsing and mutation detection."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.errors import AuthenticationError
from app.domain.models import (
    CalendarAttendee,
    CalendarEvent,
    CalendarEventPage,
    CalendarEventTime,
    EmailAddress,
    GmailMessage,
    GmailMessagePage,
    GmailMessageSummary,
)
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import ApprovalRequest, AssistantRun
from app.services.retrieval.synthesis import SynthesisResult
from app.services.routing.query import (
    QueryOrchestrator,
    build_gmail_search_query,
    calendar_mutation_kind,
    fallback_summarize_emails,
    infer_calendar_window,
    infer_event_summary,
    infer_past_calendar_window,
    is_communication_mutation,
    parse_event_times,
    retrieval_requester_id,
)

NOW = datetime(2026, 9, 10, 15, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))


def test_infer_window_tomorrow() -> None:
    start, end = infer_calendar_window("Lịch ngày mai?", now=NOW)
    assert start.isoformat() == "2026-09-11T00:00:00+07:00"
    assert end.isoformat() == "2026-09-12T00:00:00+07:00"


def test_infer_window_today() -> None:
    start, end = infer_calendar_window("Lịch hôm nay", now=NOW)
    assert start.date().isoformat() == "2026-09-10"
    assert (end - start).days == 1


def test_infer_window_this_week() -> None:
    start, end = infer_calendar_window("Lịch tuần này", now=NOW)
    assert start.isoformat() == "2026-09-07T00:00:00+07:00"
    assert end.isoformat() == "2026-09-14T00:00:00+07:00"


def test_infer_window_default_seven_days() -> None:
    start, end = infer_calendar_window("Xem lịch của tôi", now=NOW)
    assert start == NOW
    assert end == datetime(2026, 9, 17, 15, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))


def test_infer_past_window_yesterday() -> None:
    start, end = infer_past_calendar_window("Follow-up họp hôm qua", now=NOW)
    assert start.isoformat() == "2026-09-09T00:00:00+07:00"
    assert end.isoformat() == "2026-09-10T00:00:00+07:00"


def test_infer_past_window_today() -> None:
    start, end = infer_past_calendar_window("Soạn thư cuộc họp hôm nay", now=NOW)
    assert start.isoformat() == "2026-09-10T00:00:00+07:00"
    assert end == NOW


def test_infer_past_window_default_fourteen_days() -> None:
    start, end = infer_past_calendar_window("Soạn thư cuộc họp", now=NOW)
    assert start == NOW - timedelta(days=14)
    assert end == NOW


def test_parse_ten_am_tomorrow() -> None:
    parsed = parse_event_times("Tạo lịch họp lúc 10h sáng mai", now=NOW)
    assert parsed is not None
    start, end = parsed
    assert start.isoformat() == "2026-09-11T10:00:00+07:00"
    assert end.isoformat() == "2026-09-11T11:00:00+07:00"


def test_parse_returns_none_without_clock_time() -> None:
    assert parse_event_times("Tạo lịch họp với Nam", now=NOW) is None


def test_calendar_mutation_kind() -> None:
    assert calendar_mutation_kind("Tạo lịch họp lúc 10h sáng mai") == "create_event"
    assert calendar_mutation_kind("Xóa lịch họp ngày mai") == "delete_event"
    assert calendar_mutation_kind("Lịch ngày mai?") is None


def test_event_summary_for_meeting() -> None:
    assert infer_event_summary("Tạo lịch họp lúc 10h sáng mai") == "Họp"


def test_event_summary_for_custom_activity() -> None:
    assert (
        infer_event_summary(
            "Tạo cuộc họp: tạo cho tôi một lịch vào 8h sáng ngày chủ nhật với lịch là đi chuyển đồ cho trà my"
        )
        == "Đi chuyển đồ cho trà my"
    )
    assert (
        infer_event_summary(
            "Tạo lịch với nội dung đá bóng cùng công ty lúc 17h"
        )
        == "Đá bóng cùng công ty"
    )


def test_parse_event_times_sunday() -> None:
    window = parse_event_times("vào 8h sáng ngày chủ nhật", now=NOW)
    assert window is not None
    start, end = window
    assert start.isoformat() == "2026-09-13T08:00:00+07:00"
    assert end.isoformat() == "2026-09-13T09:00:00+07:00"


def test_communication_mutation_detection() -> None:
    assert is_communication_mutation("Gửi email cho Nam")
    assert not is_communication_mutation("Đọc email mới nhất từ anh Nam")
    assert not is_communication_mutation("Đọc email gửi đến hôm nay")


def test_gmail_search_query_latest_from_person() -> None:
    query, page_size = build_gmail_search_query("Đọc email mới nhất từ anh Nam")
    assert query == "in:inbox from:nam"
    assert page_size == 5


def test_gmail_search_query_unread_today() -> None:
    query, page_size = build_gmail_search_query("Email chưa đọc hôm nay")
    assert "is:unread" in query
    assert "newer_than:1d" in query
    assert page_size == 8


def test_gmail_search_query_skips_self_owner() -> None:
    query, _page_size = build_gmail_search_query("Đọc email của tôi")
    assert query == "in:inbox"


def test_retrieval_requester_id_skips_non_uuid() -> None:
    assert retrieval_requester_id("default-user") is None
    assert retrieval_requester_id("00000000-0000-0000-0000-000000000001") == (
        "00000000-0000-0000-0000-000000000001"
    )


@pytest.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


class _FakeCalendar:
    def __init__(self, page: CalendarEventPage) -> None:
        self.page = page
        self.calls: list[dict[str, object]] = []

    async def list_events(self, *args: object, **kwargs: object) -> CalendarEventPage:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.page


class _FakeGmail:
    def __init__(self) -> None:
        self.searches: list[tuple[str, int]] = []

    async def search_messages(self, query: str = "", *, page_size: int = 100, **kwargs: object):
        del kwargs
        self.searches.append((query, page_size))
        return GmailMessagePage(
            items=[GmailMessageSummary(id="m1", thread_id="t1", snippet="hello")]
        )

    async def get_message(self, message_id: str, *, format: str = "full") -> GmailMessage:
        del format
        return GmailMessage(
            id=message_id,
            thread_id="t1",
            snippet="Standup moved to 10h",
            subject="Standup notes",
            sender=EmailAddress(email="nam@example.com", display_name="Nam"),
            internal_date=datetime(2026, 9, 10, 8, 0, tzinfo=ZoneInfo("UTC")),
            label_ids=["UNREAD", "INBOX"],
        )


@pytest.mark.asyncio
async def test_orchestrator_lists_calendar_events(db_session: AsyncSession) -> None:
    start = datetime(2026, 9, 11, 9, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    end = datetime(2026, 9, 11, 10, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    page = CalendarEventPage(
        items=[
            CalendarEvent(
                id="evt-1",
                summary="Standup",
                start=CalendarEventTime(value=start, time_zone="Asia/Ho_Chi_Minh"),
                end=CalendarEventTime(value=end, time_zone="Asia/Ho_Chi_Minh"),
            )
        ]
    )
    fake = _FakeCalendar(page)

    async def calendar_for_user(_session: AsyncSession, _user_id: str) -> _FakeCalendar:
        return fake

    orchestrator = QueryOrchestrator(
        calendar_for_user=calendar_for_user,
        clock=lambda: NOW,
        new_run_id=lambda: "run-cal-1",
    )
    result = await orchestrator.handle(db_session, "user_bob", "Lịch ngày mai?")
    assert result.status == "completed"
    assert result.route.target_agent == "CalendarAgent"
    assert result.data["count"] == 1
    assert "Standup" in result.message
    run = await db_session.get(AssistantRun, "run-cal-1")
    assert run is not None
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_orchestrator_creates_approval_for_booking(db_session: AsyncSession) -> None:
    orchestrator = QueryOrchestrator(
        clock=lambda: NOW,
        new_run_id=lambda: "run-book-1",
    )
    result = await orchestrator.handle(db_session, "user_bob", "Tạo lịch họp lúc 10h sáng mai")
    assert result.status == "needs_approval"
    assert result.approval_id
    assert result.data["start"] == "2026-09-11T10:00:00+07:00"
    assert "xác nhận" in result.message.casefold()
    approval = await db_session.get(ApprovalRequest, result.approval_id)
    assert approval is not None
    assert approval.status == "pending"
    run = await db_session.get(AssistantRun, "run-book-1")
    assert run is not None
    assert run.status == "waiting_approval"


@pytest.mark.asyncio
async def test_orchestrator_retrieves_knowledge(db_session: AsyncSession) -> None:
    async def retrieve(query: str, user_id: str) -> SynthesisResult:
        assert "hybrid" in query.casefold()
        assert user_id == "user_bob"
        return SynthesisResult(
            answer="Hybrid retrieval kết hợp BM25 và vector search.",
            status=SufficiencyStatus.SUFFICIENT,
        )

    orchestrator = QueryOrchestrator(
        retrieve=retrieve,
        new_run_id=lambda: "run-rag-1",
    )
    result = await orchestrator.handle(
        db_session,
        "user_bob",
        "Tài liệu nội bộ nói gì về hybrid retrieval?",
    )
    assert result.status == "completed"
    assert result.route.target_agent == "KnowledgeResearchAgent"
    assert "BM25" in result.message


@pytest.mark.asyncio
async def test_orchestrator_blocks_when_google_disconnected(db_session: AsyncSession) -> None:
    async def calendar_for_user(_session: AsyncSession, _user_id: str) -> _FakeCalendar:
        raise AuthenticationError("Google is not connected.")

    orchestrator = QueryOrchestrator(
        calendar_for_user=calendar_for_user,
        clock=lambda: NOW,
        new_run_id=lambda: "run-block-1",
    )
    result = await orchestrator.handle(db_session, "user_bob", "Lịch ngày mai?")
    assert result.status == "blocked"
    assert "auth/google/start" in result.message


@pytest.mark.asyncio
async def test_orchestrator_rejects_jailbreak(db_session: AsyncSession) -> None:
    orchestrator = QueryOrchestrator(new_run_id=lambda: "run-rej-1")
    result = await orchestrator.handle(db_session, "user_bob", "jailbreak tài liệu nội bộ")
    assert result.status == "rejected"
    assert result.route.route_type == "reject"


@pytest.mark.asyncio
async def test_orchestrator_reads_gmail_inbox(db_session: AsyncSession) -> None:
    fake = _FakeGmail()

    async def communication_for_user(_session: AsyncSession, _user_id: str) -> _FakeGmail:
        return fake

    orchestrator = QueryOrchestrator(
        communication_for_user=communication_for_user,
        new_run_id=lambda: "run-mail-1",
    )
    result = await orchestrator.handle(db_session, "user_bob", "Đọc email mới nhất từ anh Nam")
    assert result.status == "completed"
    assert result.route.target_agent == "CommunicationAgent"
    assert fake.searches == [("in:inbox from:nam", 5)]
    assert result.data["count"] == 1
    assert result.data["messages"][0]["subject"] == "Standup notes"
    assert "Nam" in result.message


def _event(
    *,
    event_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    attendees: list[CalendarAttendee] | None = None,
) -> CalendarEvent:
    return CalendarEvent(
        id=event_id,
        summary=summary,
        start=CalendarEventTime(value=start, time_zone="Asia/Ho_Chi_Minh"),
        end=CalendarEventTime(value=end, time_zone="Asia/Ho_Chi_Minh"),
        attendees=attendees or [],
    )


@pytest.mark.asyncio
async def test_orchestrator_runs_meeting_prep(db_session: AsyncSession) -> None:
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    page = CalendarEventPage(
        items=[
            _event(
                event_id="past-1",
                summary="Old standup",
                start=datetime(2026, 9, 9, 9, 0, tzinfo=zone),
                end=datetime(2026, 9, 9, 10, 0, tzinfo=zone),
            ),
            _event(
                event_id="evt-nam",
                summary="Họp với Nam",
                start=datetime(2026, 9, 11, 9, 0, tzinfo=zone),
                end=datetime(2026, 9, 11, 10, 0, tzinfo=zone),
                attendees=[
                    CalendarAttendee(email="nam@example.com"),
                    CalendarAttendee(email="me@example.com", self_attendee=True),
                ],
            ),
        ]
    )
    fake_cal = _FakeCalendar(page)
    fake_mail = _FakeGmail()

    async def calendar_for_user(_session: AsyncSession, _user_id: str) -> _FakeCalendar:
        return fake_cal

    async def communication_for_user(_session: AsyncSession, _user_id: str) -> _FakeGmail:
        return fake_mail

    async def retrieve(query: str, user_id: str) -> SynthesisResult:
        assert "Nam" in query or "họp" in query.casefold()
        assert user_id == "user_bob"
        return SynthesisResult(
            answer="Quy chế họp yêu cầu gửi agenda trước 24 giờ.",
            status=SufficiencyStatus.SUFFICIENT,
        )

    orchestrator = QueryOrchestrator(
        calendar_for_user=calendar_for_user,
        communication_for_user=communication_for_user,
        retrieve=retrieve,
        clock=lambda: NOW,
        new_run_id=lambda: "run-prep-1",
    )
    result = await orchestrator.handle(db_session, "user_bob", "Chuẩn bị họp ngày mai với Nam")
    assert result.status == "completed"
    assert result.route.target_workflow_id == "WF-05"
    assert result.data["meeting"]["id"] == "evt-nam"
    assert "Hồ sơ họp" in result.message
    assert "nam@example.com" in result.message
    assert "agenda" in result.message.casefold() or "Quy chế" in result.message
    assert fake_mail.searches
    run = await db_session.get(AssistantRun, "run-prep-1")
    assert run is not None
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_orchestrator_drafts_meeting_followup(db_session: AsyncSession) -> None:
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    page = CalendarEventPage(
        items=[
            _event(
                event_id="future-1",
                summary="Họp tuần sau",
                start=datetime(2026, 9, 12, 9, 0, tzinfo=zone),
                end=datetime(2026, 9, 12, 10, 0, tzinfo=zone),
                attendees=[CalendarAttendee(email="later@example.com")],
            ),
            _event(
                event_id="past-board",
                summary="Ban giám đốc",
                start=datetime(2026, 9, 9, 11, 0, tzinfo=zone),
                end=datetime(2026, 9, 9, 12, 0, tzinfo=zone),
                attendees=[
                    CalendarAttendee(email="gd@example.com"),
                    CalendarAttendee(email="me@example.com", organizer=True),
                ],
            ),
        ]
    )
    fake_cal = _FakeCalendar(page)
    fake_mail = _FakeGmail()

    async def calendar_for_user(_session: AsyncSession, _user_id: str) -> _FakeCalendar:
        return fake_cal

    async def communication_for_user(_session: AsyncSession, _user_id: str) -> _FakeGmail:
        return fake_mail

    orchestrator = QueryOrchestrator(
        calendar_for_user=calendar_for_user,
        communication_for_user=communication_for_user,
        clock=lambda: NOW,
        new_run_id=lambda: "run-follow-1",
    )
    result = await orchestrator.handle(db_session, "user_bob", "Soạn thư cuộc họp")
    assert result.status == "needs_approval"
    assert result.route.target_workflow_id == "WF-01"
    assert result.approval_id
    assert result.data["meeting_id"] == "past-board"
    assert result.data["to"] == ["gd@example.com"]
    approval = await db_session.get(ApprovalRequest, result.approval_id)
    assert approval is not None
    assert approval.tool_name == "gmail.create_draft"
    assert approval.parameters["to"] == ["gd@example.com"]
    assert approval.parameters["subject"] == "Follow-up: Ban giám đốc"
    run = await db_session.get(AssistantRun, "run-follow-1")
    assert run is not None
    assert run.status == "waiting_approval"


@pytest.mark.asyncio
async def test_orchestrator_leaves_document_briefing_routed(db_session: AsyncSession) -> None:
    orchestrator = QueryOrchestrator(new_run_id=lambda: "run-wf02-1")
    result = await orchestrator.handle(
        db_session,
        "user_bob",
        "Hãy tra cứu và tóm tắt tài liệu kiến trúc hệ thống",
    )
    assert result.status == "routed"
    assert result.route.target_workflow_id == "WF-02"


def test_fallback_summarize_emails_categorizes_correctly() -> None:
    sample = [
        {
            "from": "Google <no-reply@accounts.google.com>",
            "subject": "Cảnh báo bảo mật",
            "when": "2026-09-11T02:54:21+00:00",
            "snippet": "Mới đăng nhập trên thiết bị Windows",
            "unread": True,
        },
        {
            "from": "LinkedIn Job Alerts",
            "subject": "AI Agent Engineer role at Binance",
            "when": "2026-09-11T01:20:47+00:00",
            "snippet": "Apply now for AI Agent Engineer",
            "unread": True,
        },
        {
            "from": "Medium Daily Digest",
            "subject": "3D Perception: LiDAR",
            "when": "2026-09-11T00:50:00+00:00",
            "snippet": "Stories for Nam Dau",
            "unread": False,
        },
    ]
    summary = fallback_summarize_emails("Đọc và tóm tắt email", sample)
    assert "Tổng quan" in summary
    assert "3 email" in summary
    assert "2 email chưa đọc" in summary
    assert "Quan trọng / Cần lưu ý ngay" in summary
    assert "Cảnh báo bảo mật" in summary
    assert "Công việc & Tuyển dụng" in summary
    assert "AI Agent Engineer role at Binance" in summary
    assert "Bản tin & Thông báo dịch vụ" in summary


@pytest.mark.asyncio
async def test_orchestrator_handles_communication_with_summarizer(
    db_session: AsyncSession,
) -> None:
    fake_mail = _FakeGmail()

    async def communication_for_user(_session: AsyncSession, _user_id: str) -> _FakeGmail:
        return fake_mail

    summarizer_called = False

    async def mock_summarizer(query: str, messages: list[dict[str, Any]]) -> str:
        nonlocal summarizer_called
        summarizer_called = True
        return f"Tóm tắt thông minh cho {len(messages)} email"

    orchestrator = QueryOrchestrator(
        communication_for_user=communication_for_user,
        summarize_emails_fn=mock_summarizer,
        new_run_id=lambda: "run-comm-sum-1",
    )
    result = await orchestrator.handle(
        db_session,
        "user_bob",
        "Đọc và tóm tắt các email quan trọng nhận được hôm nay",
    )
    assert result.status == "completed"
    assert result.route.target_agent == "CommunicationAgent"
    assert summarizer_called is True
    assert "Tóm tắt thông minh cho 1 email" in result.message
    assert result.data["count"] == 1
    assert len(result.data["messages"]) == 1
