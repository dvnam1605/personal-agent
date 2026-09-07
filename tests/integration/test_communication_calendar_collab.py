"""Collaboration flow: recent email -> follow-up meeting slot + proposal (§6.2).

Simulated multi-turn interaction with scripted chat backends and deterministic
tool doubles (no LLM, no network). The slot search runs under a read-only
tool view; the booking itself only exists as a fail-closed ProposedAction.
"""

from app.agents import (
    CALENDAR_AGENT_NAME,
    COMMUNICATION_AGENT_NAME,
    build_first_party_registry,
)
from app.agents.specialist.calendar import build_event_proposal, slot_search_task
from app.agents.specialist.communication import latest_email_task
from app.agents.specialist.react import SpecialistRunner
from app.domain.enums import SpecialistStatus
from app.services.capability_gate import CapabilityGate
from app.tools import (
    CALENDAR_TOOL_DEFINITIONS,
    COMMUNICATION_TOOL_DEFINITIONS,
    ToolRegistry,
)
from tests.unit.agents import _fakes as fakes
from tests.unit.agents._fakes import DictExecutor, ScriptedChat


def _gate() -> CapabilityGate:
    registry = ToolRegistry([*COMMUNICATION_TOOL_DEFINITIONS, *CALENDAR_TOOL_DEFINITIONS])
    return CapabilityGate(registry, build_first_party_registry())


async def test_email_to_follow_up_meeting_flow() -> None:
    gate = _gate()
    agents = build_first_party_registry()

    # Turn 1: user asks for the recent email -> CommunicationAgent direct read.
    comm_chat = ScriptedChat(
        [fakes.text_turn("Email mới nhất của Nam: chốt dùng reranker nội bộ, cần họp follow-up.")]
    )
    comm_runner = SpecialistRunner(comm_chat, DictExecutor({}))
    email_outcome = await comm_runner.run(
        latest_email_task("Nam"),
        agents.get(COMMUNICATION_AGENT_NAME),
        gate.for_agent(COMMUNICATION_AGENT_NAME),
        run_id="p12-e2e-1",
        user_id="u1",
    )
    assert email_outcome.report.status is SpecialistStatus.SUCCESS
    assert email_outcome.usage.llm_calls == 1

    # Turn 2: schedule a 45-minute follow-up -> CalendarAgent slot search (read-only).
    read_only = gate.read_only_view(CALENDAR_AGENT_NAME)
    assert "calendar.find_free_slots" in read_only.tool_names
    assert "calendar.create_event" not in read_only.tool_names

    slot_chat = ScriptedChat(
        [
            fakes.calls_turn(("calendar.get_free_busy", {"calendars": ["nam@example.com"]})),
            fakes.calls_turn(("calendar.find_free_slots", {"duration_minutes": 45})),
            fakes.report_turn(
                status="needs_approval",
                summary="Slot trống: thứ hai 9h00-9h45, chờ duyệt thư mời.",
                extra={
                    "data": {
                        "slot": "2026-09-14T09:00:00+07:00",
                        "proposal_action": "create_event",
                    }
                },
            ),
        ]
    )
    slot_executor = DictExecutor(
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
    slot_runner = SpecialistRunner(slot_chat, slot_executor)
    slot_outcome = await slot_runner.run(
        slot_search_task(
            duration_minutes=45,
            time_min="2026-09-14T00:00:00+07:00",
            time_max="2026-09-18T23:59:59+07:00",
            attendee_emails=["nam@example.com"],
        ),
        agents.get(CALENDAR_AGENT_NAME),
        read_only,
        run_id="p12-e2e-2",
        user_id="u1",
    )
    assert slot_outcome.report.status is SpecialistStatus.NEEDS_APPROVAL
    assert slot_outcome.needs_approval is True
    assert slot_outcome.report.data["slot"] == "2026-09-14T09:00:00+07:00"
    assert [call[0] for call in slot_executor.calls] == [
        "calendar.get_free_busy",
        "calendar.find_free_slots",
    ]

    # Turn 3: the draft invitation exists only as a fail-closed proposal.
    proposal = build_event_proposal(
        summary="Họp follow-up RAG",
        start="2026-09-14T09:00:00+07:00",
        end="2026-09-14T09:45:00+07:00",
        attendee_emails=["nam@example.com"],
    )
    assert proposal.tool_name == "calendar.create_event"
    assert proposal.requires_approval is True
