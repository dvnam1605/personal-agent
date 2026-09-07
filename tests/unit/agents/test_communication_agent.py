"""Unit tests for the CommunicationAgent domain layer (spec P12A §6.1)."""

import pytest

from app.agents import COMMUNICATION_AGENT_NAME, build_first_party_registry
from app.agents.specialist.communication import (
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
    task = latest_email_task("Nam")
    assert ModeSelector.select(task, _agent()) is ExecutionMode.DIRECT

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


def test_communication_task_builders_reject_blank_input() -> None:
    with pytest.raises(ValidationError):
        latest_email_task("   ")
    with pytest.raises(ValidationError):
        thread_read_task("")
    with pytest.raises(ValidationError):
        contact_lookup_task("")
    with pytest.raises(ValidationError):
        build_send_proposal(recipients=[], subject="s", body="b")
    with pytest.raises(ValidationError):
        build_send_proposal(recipients=["a@x.com"], subject=" ", body="b")
