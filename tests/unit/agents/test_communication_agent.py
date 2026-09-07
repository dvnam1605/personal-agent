"""Unit tests for the CommunicationAgent domain layer (spec P12A §6.1)."""

import pytest

from app.agents import COMMUNICATION_AGENT_NAME, build_first_party_registry
from app.agents.specialist.communication import (
    build_message_action_proposal,
    build_send_proposal,
    complex_task,
    contact_lookup_task,
    disambiguation_report,
    latest_email_task,
    thread_read_task,
)
from app.agents.specialist.react import ModeSelector, SpecialistRunner
from app.domain.enums import ActionRiskLevel, ExecutionMode, SpecialistStatus
from app.domain.errors import ValidationError
from app.services.capability_gate import CapabilityGate
from app.tools import COMMUNICATION_TOOL_DEFINITIONS, ToolRegistry
from tests.unit.agents import _fakes as fakes
from tests.unit.agents._fakes import DictExecutor, ScriptedChat


def _harness(chat: ScriptedChat, executor: DictExecutor) -> tuple[SpecialistRunner, CapabilityGate]:
    registry = ToolRegistry([*COMMUNICATION_TOOL_DEFINITIONS])
    gate = CapabilityGate(registry, build_first_party_registry())
    return SpecialistRunner(chat, executor), gate


def _agent():
    return build_first_party_registry().get(COMMUNICATION_AGENT_NAME)


async def test_communication_agent_direct_read() -> None:
    """Exact email lookup bypasses the ReAct loop (0 tool iterations)."""
    task = latest_email_task(
        "Nam", context_data={"contact_email": "nam@example.com", "hint": "họp RAG thứ sáu"}
    )
    assert ModeSelector.select(task, _agent()) is ExecutionMode.DIRECT
    assert task.context_data["contact_email"] == "nam@example.com"
    assert "KHÔNG gọi" in task.goal

    chat = ScriptedChat([fakes.text_turn("Email mới nhất của Nam: họp RAG thứ sáu.")])
    runner, gate = _harness(chat, DictExecutor({}))
    outcome = await runner.run(
        task, _agent(), gate.for_agent(COMMUNICATION_AGENT_NAME), run_id="p12-c1", user_id="u1"
    )

    assert outcome.report.status is SpecialistStatus.SUCCESS
    assert outcome.usage.llm_calls == 1
    assert outcome.usage.react_steps == 0
    assert outcome.trace.steps == []
    assert outcome.trace.escalated_to_react is False


async def test_communication_agent_complex_react_within_budget() -> None:
    """Spec §3.2 flow finishes in <= 4 iterations and <= 3000 prompt tokens."""
    task = complex_task("Tìm email Nam gửi gần đây về RAG và tóm tắt quyết định.")
    assert ModeSelector.select(task, _agent()) is ExecutionMode.BOUNDED_REACT
    assert task.budget.max_react_steps == 4
    assert task.budget.max_prompt_tokens == 3000

    chat = ScriptedChat(
        [
            fakes.calls_turn(("contacts.resolve_person", {"name": "Nam"})),
            fakes.calls_turn(("gmail.search_messages", {"query": "from:nam@example.com RAG"})),
            fakes.calls_turn(("gmail.get_thread", {"thread_id": "thread-1"})),
            fakes.report_turn(summary="Quyết định cuối: dùng reranker nội bộ."),
        ]
    )
    executor = DictExecutor(
        {
            "contacts.resolve_person": lambda args: fakes.ok_result(
                "contacts.resolve_person", {"email": "nam@example.com"}
            ),
            "gmail.search_messages": lambda args: fakes.ok_result(
                "gmail.search_messages", {"messages": [{"id": "m1", "thread_id": "thread-1"}]}
            ),
            "gmail.get_thread": lambda args: fakes.ok_result(
                "gmail.get_thread", {"id": "thread-1", "messages": ["quyết định X"]}
            ),
        }
    )
    runner, gate = _harness(chat, executor)
    outcome = await runner.run(
        task, _agent(), gate.for_agent(COMMUNICATION_AGENT_NAME), run_id="p12-c2", user_id="u1"
    )

    assert outcome.report.status is SpecialistStatus.SUCCESS
    assert outcome.usage.react_steps <= 4
    assert outcome.usage.prompt_tokens <= 3000
    assert [call[0] for call in executor.calls] == [
        "contacts.resolve_person",
        "gmail.search_messages",
        "gmail.get_thread",
    ]


def test_communication_agent_contact_disambiguation() -> None:
    """Multiple candidates produce clarification, never a guess."""
    report = disambiguation_report(
        [
            {"name": "Nam Nguyen", "email": "nam.nguyen@example.com"},
            {"name": "Nam Tran", "email": "nam.tran@example.com"},
        ]
    )
    assert report.status is SpecialistStatus.NEEDS_MORE_CONTEXT
    assert "Nam Nguyen <nam.nguyen@example.com>" in report.summary
    assert "Nam Tran <nam.tran@example.com>" in report.summary
    assert report.missing_context is not None and len(report.missing_context) == 2


def test_communication_agent_contact_not_found_asks_for_email() -> None:
    report = disambiguation_report([])
    assert report.status is SpecialistStatus.NEEDS_MORE_CONTEXT
    assert report.missing_context == ["contact_email: manual email input required"]


def test_communication_agent_single_candidate_is_unique_match() -> None:
    report = disambiguation_report([{"name": "Nam Nguyen", "email": "nam@example.com"}])
    assert report.status is SpecialistStatus.SUCCESS
    assert "duy nhất" in report.summary
    assert "Nam Nguyen <nam@example.com>" in report.summary


def test_communication_agent_disambiguation_rejects_bad_shapes() -> None:
    with pytest.raises(ValidationError):
        disambiguation_report("Nam")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        disambiguation_report(["not-a-dict"])  # type: ignore[list-item]


def test_communication_agent_draft_generates_proposal() -> None:
    """Write intent produces a fail-closed ProposedAction, not a live send."""
    long_body = "Nội dung họp. " * 500
    proposal = build_send_proposal(
        recipients=["nam@example.com"],
        subject="Xác nhận lịch họp RAG",
        body=long_body,
    )
    assert proposal.tool_name == "gmail.send_draft"
    assert proposal.action_type == "send_email"
    assert proposal.risk_level is ActionRiskLevel.HIGH_IMPACT_WRITE
    assert proposal.requires_approval is True
    assert proposal.parameters["to"] == ["nam@example.com"]
    assert proposal.parameters["subject"] == "Xác nhận lịch họp RAG"
    assert "[TRUNCATED]" in str(proposal.parameters["body_preview"])
    assert "nam@example.com" in proposal.description


def test_communication_agent_reply_forward_target_real_tools() -> None:
    reply = build_send_proposal(
        recipients=["nam@example.com"],
        subject="Re: họp",
        body="Đồng ý.",
        message_id="m1",
        action_type="reply",
    )
    assert reply.tool_name == "gmail.reply"
    assert reply.parameters["message_id"] == "m1"

    forward = build_send_proposal(
        recipients=["lan@example.com"],
        subject="Fwd: họp",
        body="Xem giúp.",
        message_id="m1",
        action_type="forward",
    )
    assert forward.tool_name == "gmail.forward"

    with pytest.raises(ValidationError):
        build_send_proposal(
            recipients=["nam@example.com"], subject="s", body="b", action_type="reply"
        )
    with pytest.raises(ValidationError):
        build_send_proposal(
            recipients=["nam@example.com"], subject="s", body="b", action_type="shred"
        )
    with pytest.raises(ValidationError):
        build_send_proposal(recipients="nam@example.com", subject="s", body="b")  # type: ignore[arg-type]


async def test_communication_agent_mutation_without_approval_blocked() -> None:
    """A live send attempt without approval stops POLICY and never executes."""
    task = complex_task("Gửi ngay email cho Nam.")
    chat = ScriptedChat(
        [fakes.calls_turn(("gmail.send_draft", {"to": ["nam@example.com"]}))],
    )
    executor = DictExecutor(
        {"gmail.send_draft": lambda args: fakes.ok_result("gmail.send_draft", {"id": "d1"})}
    )
    runner, gate = _harness(chat, executor)
    outcome = await runner.run(
        task, _agent(), gate.for_agent(COMMUNICATION_AGENT_NAME), run_id="p12-c3", user_id="u1"
    )

    assert outcome.report.status is SpecialistStatus.NEEDS_APPROVAL
    assert outcome.needs_approval is True
    assert outcome.trace.stop_reason.value == "policy"
    assert executor.calls == []


async def test_communication_agent_contact_lookup_direct_read() -> None:
    """Contact lookup runs DIRECT from pre-resolved context (0 tool iterations)."""
    task = contact_lookup_task(
        "Nam", context_data={"name": "Nam Nguyen", "email": "nam@example.com"}
    )
    assert ModeSelector.select(task, _agent()) is ExecutionMode.DIRECT
    assert "KHÔNG gọi tool" in task.goal
    assert task.context_data["name"] == "Nam Nguyen"

    chat = ScriptedChat([fakes.text_turn("Thông tin liên hệ: Nam Nguyen <nam@example.com>.")])
    runner, gate = _harness(chat, DictExecutor({}))
    outcome = await runner.run(
        task,
        _agent(),
        gate.for_agent(COMMUNICATION_AGENT_NAME),
        run_id="p12-c-contact",
        user_id="u1",
    )

    assert outcome.report.status is SpecialistStatus.SUCCESS
    assert outcome.usage.llm_calls == 1
    assert outcome.usage.react_steps == 0
    assert outcome.trace.escalated_to_react is False


async def test_communication_agent_thread_read_direct() -> None:
    """Thread summary runs DIRECT from pre-resolved context (0 tool iterations)."""
    task = thread_read_task("t-123", context_data={"thread_id": "t-123", "messages": ["m1", "m2"]})
    assert ModeSelector.select(task, _agent()) is ExecutionMode.DIRECT
    assert "KHÔNG gọi tool" in task.goal

    chat = ScriptedChat([fakes.text_turn("Tóm tắt thread t-123: thảo luận kiến trúc P12.")])
    runner, gate = _harness(chat, DictExecutor({}))
    outcome = await runner.run(
        task,
        _agent(),
        gate.for_agent(COMMUNICATION_AGENT_NAME),
        run_id="p12-c-thread",
        user_id="u1",
    )

    assert outcome.report.status is SpecialistStatus.SUCCESS
    assert outcome.usage.llm_calls == 1
    assert outcome.usage.react_steps == 0
    assert outcome.trace.escalated_to_react is False


def test_communication_agent_send_draft_with_draft_id() -> None:
    proposal = build_send_proposal(
        recipients=["nam@example.com"],
        subject="Re: thảo luận",
        body="Đã gửi.",
        draft_id="d-123",
        action_type="send_email",
    )
    assert proposal.tool_name == "gmail.send_draft"
    assert proposal.parameters["draft_id"] == "d-123"


def test_communication_agent_message_action_proposals() -> None:
    archive = build_message_action_proposal(message_id="m1", action_type="archive")
    assert archive.tool_name == "gmail.archive"
    assert archive.parameters["message_id"] == "m1"
    assert archive.risk_level is ActionRiskLevel.LOW_IMPACT_WRITE

    trash = build_message_action_proposal(message_id="m1", action_type="trash")
    assert trash.tool_name == "gmail.trash"
    assert trash.parameters["message_id"] == "m1"
    assert trash.risk_level is ActionRiskLevel.IRREVERSIBLE

    delete_draft = build_message_action_proposal(draft_id="d1", action_type="delete_draft")
    assert delete_draft.tool_name == "gmail.delete_draft"
    assert delete_draft.parameters["draft_id"] == "d1"
    assert delete_draft.risk_level is ActionRiskLevel.IRREVERSIBLE

    with pytest.raises(ValidationError):
        build_message_action_proposal(action_type="archive")
    with pytest.raises(ValidationError):
        build_message_action_proposal(action_type="delete_draft")
    with pytest.raises(ValidationError):
        build_message_action_proposal(message_id="m1", action_type="explode")


def test_communication_task_builders_reject_blank_input() -> None:
    with pytest.raises(ValidationError):
        latest_email_task("   ")
    with pytest.raises(ValidationError):
        latest_email_task("Nam", page_size=0)
    with pytest.raises(ValidationError):
        latest_email_task("Nam", page_size=True)  # type: ignore[arg-type]
    task_float = latest_email_task("Nam", page_size=2.0)  # type: ignore[arg-type]
    assert "page_size=2" in task_float.goal
    with pytest.raises(ValidationError):
        latest_email_task("Nam", page_size=2.5)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        thread_read_task("")
    with pytest.raises(ValidationError):
        contact_lookup_task("")
    with pytest.raises(ValidationError):
        build_send_proposal(recipients=[], subject="s", body="b")
    with pytest.raises(ValidationError):
        build_send_proposal(recipients=["a@x.com"], subject=" ", body="b")
