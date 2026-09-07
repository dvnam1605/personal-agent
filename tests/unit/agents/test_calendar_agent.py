"""Unit tests for the CalendarAgent domain layer (spec P12B §6.1)."""

import pytest

from app.agents import CALENDAR_AGENT_NAME, build_first_party_registry
from app.agents.specialist.calendar import (
    build_event_proposal,
    complex_task,
    no_availability_report,
    schedule_query_task,
    slot_search_task,
)
from app.agents.specialist.react import ModeSelector, SpecialistRunner
from app.domain.enums import ActionRiskLevel, ExecutionMode, SpecialistStatus
from app.domain.errors import ValidationError
from app.services.capability_gate import CapabilityGate
from app.tools import CALENDAR_TOOL_DEFINITIONS, ToolRegistry
from tests.unit.agents import _fakes as fakes
from tests.unit.agents._fakes import DictExecutor, ScriptedChat

TIME_MIN = "2026-09-08T00:00:00+07:00"
TIME_MAX = "2026-09-08T23:59:59+07:00"


def _harness(chat: ScriptedChat, executor: DictExecutor) -> tuple[SpecialistRunner, CapabilityGate]:
    registry = ToolRegistry([*CALENDAR_TOOL_DEFINITIONS])
    gate = CapabilityGate(registry, build_first_party_registry())
    return SpecialistRunner(chat, executor), gate


def _agent():
    return build_first_party_registry().get(CALENDAR_AGENT_NAME)


async def test_calendar_agent_direct_schedule_query() -> None:
    """ "Lịch ngày mai" executes in one step without entering ReAct."""
    task = schedule_query_task(
        "ngày mai",
        time_min=TIME_MIN,
        time_max=TIME_MAX,
        context_data={"events": ["standup 9h", "review 15h"]},
    )
    assert ModeSelector.select(task, _agent()) is ExecutionMode.DIRECT
    assert "KHÔNG gọi" in task.goal

    chat = ScriptedChat([fakes.text_turn("Ngày mai có 2 sự kiện: standup 9h, review 15h.")])
    runner, gate = _harness(chat, DictExecutor({}))
    outcome = await runner.run(
        task, _agent(), gate.for_agent(CALENDAR_AGENT_NAME), run_id="p12-k1", user_id="u1"
    )

    assert outcome.report.status is SpecialistStatus.SUCCESS
    assert outcome.usage.llm_calls == 1
    assert outcome.usage.react_steps == 0
    assert outcome.trace.escalated_to_react is False


async def test_calendar_agent_slot_finding_deterministic() -> None:
    """Slot search delegates to find_free_slots instead of LLM arithmetic."""
    task = slot_search_task(
        duration_minutes=45,
        time_min="2026-09-14T00:00:00+07:00",
        time_max="2026-09-18T23:59:59+07:00",
        attendee_emails=["nam@example.com"],
    )
    assert task.budget.max_react_steps == 4
    assert task.budget.max_prompt_tokens == 3000

    chat = ScriptedChat(
        [
            fakes.calls_turn(
                (
                    "calendar.get_free_busy",
                    {"time_min": "2026-09-14T00:00:00+07:00", "calendars": ["nam@example.com"]},
                )
            ),
            fakes.calls_turn(
                (
                    "calendar.find_free_slots",
                    {"duration_minutes": 45, "preferred_time_of_day": "morning"},
                )
            ),
            fakes.report_turn(summary="Slot trống: thứ hai 9h00-9h45."),
        ]
    )
    executor = DictExecutor(
        {
            "calendar.get_free_busy": lambda args: fakes.ok_result(
                "calendar.get_free_busy", {"busy": []}
            ),
            "calendar.find_free_slots": lambda args: fakes.ok_result(
                "calendar.find_free_slots",
                {"slots": [{"start": "2026-09-14T09:00:00+07:00"}]},
            ),
        }
    )
    runner, gate = _harness(chat, executor)
    outcome = await runner.run(
        task, _agent(), gate.for_agent(CALENDAR_AGENT_NAME), run_id="p12-k2", user_id="u1"
    )

    assert outcome.report.status is SpecialistStatus.SUCCESS
    assert outcome.usage.react_steps <= 4
    assert [call[0] for call in executor.calls] == [
        "calendar.get_free_busy",
        "calendar.find_free_slots",
    ]
    # The task goal pins the deterministic parameters for the LLM call.
    assert "duration_minutes=45" in task.goal
    assert "working_hours_start=08:30" in task.goal
    assert "working_hours_end=17:30" in task.goal
    assert "không tự tính slot" in task.goal


def test_calendar_agent_create_event_proposal() -> None:
    """Event booking generates a fail-closed ProposedAction."""
    proposal = build_event_proposal(
        summary="Họp follow-up RAG",
        start="2026-09-14T09:00:00+07:00",
        end="2026-09-14T09:45:00+07:00",
        attendee_emails=["nam@example.com"],
        location="Phòng A",
        description="Chi tiết họp follow-up",
    )
    assert proposal.tool_name == "calendar.create_event"
    assert proposal.action_type == "create_event"
    assert proposal.risk_level is ActionRiskLevel.HIGH_IMPACT_WRITE
    assert proposal.requires_approval is True
    assert proposal.parameters["summary"] == "Họp follow-up RAG"
    assert proposal.parameters["location"] == "Phòng A"
    assert proposal.parameters["description"] == "Chi tiết họp follow-up"
    assert proposal.parameters["attendees"] == ["nam@example.com"]
    assert proposal.parameters["timezone"] == "Asia/Ho_Chi_Minh"
    assert "nam@example.com" in proposal.description


def test_calendar_agent_mutation_proposals_target_real_tools() -> None:
    update = build_event_proposal(event_id="e1", summary="Họp dời 10h", action_type="update_event")
    assert update.tool_name == "calendar.update_event"
    assert update.parameters["event_id"] == "e1"
    assert update.risk_level is ActionRiskLevel.HIGH_IMPACT_WRITE

    delete = build_event_proposal(event_id="e1", action_type="delete_event")
    assert delete.tool_name == "calendar.delete_event"
    assert delete.risk_level is ActionRiskLevel.IRREVERSIBLE

    add = build_event_proposal(
        event_id="e1",
        attendee_emails=["lan@example.com"],
        action_type="add_attendee",
    )
    assert add.tool_name == "calendar.add_attendee"

    with pytest.raises(ValidationError):
        build_event_proposal(event_id="e1", action_type="update_event")
    with pytest.raises(ValidationError):
        build_event_proposal(summary="Họp", action_type="update_event")
    with pytest.raises(ValidationError):
        build_event_proposal(event_id="e1", action_type="add_attendee")
    with pytest.raises(ValidationError):
        build_event_proposal(
            start="2026-09-14T09:00:00+07:00",
            end="2026-09-14T09:45:00+07:00",
            action_type="create_event",
        )
    with pytest.raises(ValidationError):
        build_event_proposal(
            event_id="e1",
            start="2026-09-14T09:00:00+07:00",
            action_type="update_event",
        )
    with pytest.raises(ValidationError):
        build_event_proposal(
            event_id="e1",
            end="2026-09-14T09:45:00+07:00",
            action_type="update_event",
        )
    with pytest.raises(ValidationError):
        build_event_proposal(
            summary="Họp",
            start="2026-09-14T09:00:00+07:00",
            end="2026-09-14T09:45:00+07:00",
            action_type="explode",
        )
    with pytest.raises(ValidationError):
        build_event_proposal(
            summary="Họp",
            start="2026-09-14T09:00:00+07:00",
            end="2026-09-14T09:45:00+07:00",
            attendee_emails="nam@example.com",  # type: ignore[arg-type]
        )


async def test_calendar_agent_mutation_without_approval_blocked() -> None:
    """A live booking attempt without approval stops POLICY and never executes."""
    task = complex_task("Đặt lịch họp ngay với Nam.")
    chat = ScriptedChat(
        [fakes.calls_turn(("calendar.create_event", {"summary": "Họp"}))],
    )
    executor = DictExecutor(
        {"calendar.create_event": lambda args: fakes.ok_result("calendar.create_event")}
    )
    runner, gate = _harness(chat, executor)
    outcome = await runner.run(
        task, _agent(), gate.for_agent(CALENDAR_AGENT_NAME), run_id="p12-k3", user_id="u1"
    )

    assert outcome.report.status is SpecialistStatus.NEEDS_APPROVAL
    assert outcome.needs_approval is True
    assert outcome.trace.stop_reason.value == "policy"
    assert executor.calls == []


def test_no_availability_report_is_success_without_guessing() -> None:
    report = no_availability_report(duration_minutes=45, window_label="tuần sau")
    assert report.status is SpecialistStatus.SUCCESS
    assert "45 phút" in report.summary
    assert report.data["window_label"] == "tuần sau"


def test_calendar_task_builders_validate_windows() -> None:
    with pytest.raises(ValidationError):
        schedule_query_task("ngày mai", time_min=TIME_MAX, time_max=TIME_MIN)
    with pytest.raises(ValidationError):
        schedule_query_task("ngày mai", time_min="tomorrow", time_max=TIME_MAX)
    with pytest.raises(ValidationError):
        # Lexically greater but chronologically earlier: lexical compare
        # would accept ("00:30" > "00:00") while 16:30Z < 17:00Z.
        schedule_query_task(
            "ngày mai",
            time_min="2026-09-08T00:00:00+07:00",
            time_max="2026-09-08T00:30:00+08:00",
        )
    with pytest.raises(ValidationError):
        # Mixed naive/aware bounds are incomparable.
        schedule_query_task("ngày mai", time_min="2026-09-08T00:00:00", time_max=TIME_MAX)
    with pytest.raises(ValidationError):
        slot_search_task(duration_minutes=0, time_min=TIME_MIN, time_max=TIME_MAX)
    with pytest.raises(ValidationError):
        slot_search_task(duration_minutes=True, time_min=TIME_MIN, time_max=TIME_MAX)  # type: ignore[arg-type]
    task_float = slot_search_task(
        duration_minutes=45.0,  # type: ignore[arg-type]
        time_min=TIME_MIN,
        time_max=TIME_MAX,
    )
    assert "duration_minutes=45" in task_float.goal
    with pytest.raises(ValidationError):
        slot_search_task(
            duration_minutes=45.5,  # type: ignore[arg-type]
            time_min=TIME_MIN,
            time_max=TIME_MAX,
        )
    with pytest.raises(ValidationError):
        slot_search_task(
            duration_minutes=45,
            time_min=TIME_MIN,
            time_max=TIME_MAX,
            working_hours_start="25:00",
        )
    with pytest.raises(ValidationError):
        slot_search_task(
            duration_minutes=45,
            time_min=TIME_MIN,
            time_max=TIME_MAX,
            working_hours_start="8h30",
        )
    with pytest.raises(ValidationError):
        slot_search_task(
            duration_minutes=45,
            time_min=TIME_MIN,
            time_max=TIME_MAX,
            working_hours_start="17:30",
            working_hours_end="08:30",
        )
    with pytest.raises(ValidationError):
        build_event_proposal(
            summary="Họp",
            start="2026-09-14T10:00:00+07:00",
            end="2026-09-14T09:00:00+07:00",
        )
    with pytest.raises(ValidationError):
        no_availability_report(duration_minutes=0, window_label="tuần sau")


async def test_calendar_tools_find_free_slots_accepts_time_min_alias() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.domain.models import ToolContext, ToolInput
    from app.tools.google_calendar import GoogleCalendarTools

    mock_service = MagicMock()
    mock_service.find_free_slots = AsyncMock(return_value=[])
    tools = GoogleCalendarTools(mock_service)
    result = await tools.execute(
        ToolInput(
            tool_name="calendar.find_free_slots",
            arguments={
                "time_min": TIME_MIN,
                "time_max": TIME_MAX,
                "duration_minutes": 30,
            },
        ),
        ToolContext(run_id="r1", user_id="u1"),
    )
    assert result.success is True
    mock_service.find_free_slots.assert_awaited_once()
