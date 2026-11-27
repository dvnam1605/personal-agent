"""Specialists propose writes instead of running them, and keep the transcript paired."""

from __future__ import annotations

from typing import Any

from app.agents import AgentRegistry
from app.agents.specialist.react import SpecialistRunner
from app.domain.models.supervisor.specialist import SpecialistTask
from app.services.context.consolidation import MAX_PENDING_TURNS, BackgroundConsolidationWorker
from app.services.routing.capability_gate import CapabilityGate
from app.tools.registry import ToolRegistry
from tests.unit.agents import _fakes as fakes
from tests.unit.agents._fakes import DictExecutor, ScriptedChat

DRAFT = {"to": ["an@example.com"], "subject": "Báo cáo", "body": "Nội dung"}


async def _run(chat: ScriptedChat, executor: DictExecutor, **runner_kw: Any) -> Any:
    registry = ToolRegistry([fakes.make_read_tool(), fakes.make_mutation_tool()])
    agent = fakes.make_agent()
    gate = CapabilityGate(registry, AgentRegistry([agent]))
    runner = SpecialistRunner(chat, executor, **runner_kw)
    task = SpecialistTask(agent_name=agent.name, goal="soạn email")
    return await runner.run(task, agent, gate.for_agent(agent.name), run_id="r1", user_id="u1")


async def test_propose_action_ends_the_task_with_a_proposal() -> None:
    chat = ScriptedChat(
        [
            fakes.calls_turn(
                (
                    "specialist.propose_action",
                    {"tool_name": "gmail.create_draft", "arguments": DRAFT},
                )
            )
        ]
    )
    outcome = await _run(chat, DictExecutor({}), offer_proposals=True)
    assert outcome.needs_approval
    assert outcome.report.data["proposed_action"] == {
        "tool_name": "gmail.create_draft",
        "arguments": DRAFT,
    }
    assert "specialist.propose_action" in chat.tool_schemas_seen[0]


async def test_propose_action_is_not_offered_by_default() -> None:
    chat = ScriptedChat([fakes.report_turn()])
    await _run(chat, DictExecutor({}))
    assert "specialist.propose_action" not in chat.tool_schemas_seen[0]


async def test_incomplete_proposal_is_sent_back_to_the_model() -> None:
    chat = ScriptedChat(
        [
            fakes.calls_turn(
                (
                    "specialist.propose_action",
                    {"tool_name": "gmail.create_draft", "arguments": {"reason": "x"}},
                )
            ),
            fakes.report_turn(),
        ]
    )
    outcome = await _run(chat, DictExecutor({}), offer_proposals=True)
    assert not outcome.needs_approval
    tool_messages = [m for m in chat.prompts[1] if m.role == "tool"]
    assert "missing" in tool_messages[-1].content
    assert tool_messages[-1].tool_call_id


async def test_every_tool_result_is_paired_with_its_call() -> None:
    chat = ScriptedChat([fakes.calls_turn(("test.search", {"q": "x"})), fakes.report_turn()])
    executor = DictExecutor({"test.search": lambda _a: fakes.ok_result("test.search")})
    await _run(chat, executor)
    second = chat.prompts[1]
    assistant = next(m for m in second if m.role == "assistant")
    tool = next(m for m in second if m.role == "tool")
    assert assistant.tool_calls and tool.tool_call_id == assistant.tool_calls[0].call_id


def test_consolidation_queue_drops_the_oldest_turn_when_full() -> None:
    worker = BackgroundConsolidationWorker(object(), object())  # type: ignore[arg-type]
    for index in range(MAX_PENDING_TURNS + 5):
        worker.enqueue_turn("u1", f"q{index}", "a")
    assert worker.pending_count == MAX_PENDING_TURNS
