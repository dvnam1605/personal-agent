"""Unit tests for the bounded ReAct runtime (spec P11-01..04/06)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.agents.specialist.react import ModeSelector, SpecialistRunner
from app.domain.enums import ExecutionMode
from app.domain.models import ExecutionBudget, SpecialistTask, ToolRestriction
from app.services.approvals import generate_approval_token
from app.services.routing.capability_gate import CapabilityGate
from app.tools.registry import ToolRegistry
from tests.unit.agents import _fakes as fakes
from tests.unit.agents._fakes import DictExecutor, ScriptedChat


def _harness(
    chat: ScriptedChat,
    executor: DictExecutor,
    tools: list | None = None,
    **runner_kw: Any,
) -> tuple[SpecialistRunner, CapabilityGate, object]:
    from app.agents import AgentRegistry

    registry = ToolRegistry(tools if tools is not None else [fakes.make_read_tool()])
    agents = AgentRegistry([fakes.make_agent()])
    gate = CapabilityGate(registry, agents)
    runner = SpecialistRunner(chat, executor, **runner_kw)
    return runner, gate, registry


def _task(**overrides: Any) -> SpecialistTask:
    data: dict[str, Any] = {"agent_name": "TestSpecialist", "goal": "test goal"}
    data.update(overrides)
    return SpecialistTask(**data)


class TestModeSelector:
    def test_explicit_task_mode_wins(self) -> None:
        agent = fakes.make_agent(mode=ExecutionMode.DIRECT)
        task = _task(mode=ExecutionMode.BOUNDED_REACT)
        assert ModeSelector.select(task, agent) is ExecutionMode.BOUNDED_REACT

    def test_agent_default_applies(self) -> None:
        agent = fakes.make_agent(mode=ExecutionMode.DIRECT)
        assert ModeSelector.select(_task(), agent) is ExecutionMode.DIRECT


class TestDirectPath:
    async def test_direct_single_llm_no_tools(self) -> None:
        chat = ScriptedChat([fakes.text_turn("forty-two")])
        executor = DictExecutor({})
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent(mode=ExecutionMode.DIRECT)
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.report.status.value == "success"
        assert outcome.report.summary == "forty-two"
        assert outcome.usage.llm_calls == 1
        assert outcome.usage.tool_calls == 0
        assert outcome.trace.steps == []
        assert executor.calls == []

    async def test_direct_escalates_to_react_when_tools_requested(self) -> None:
        chat = ScriptedChat(
            [
                fakes.calls_turn(("test.search", {"q": "x"})),
                fakes.calls_turn(("test.search", {"q": "y"})),
                fakes.report_turn(summary="found it"),
            ]
        )
        executor = DictExecutor({"test.search": lambda args: fakes.ok_result("test.search")})
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent(mode=ExecutionMode.DIRECT)
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.escalated_to_react is True
        assert outcome.report.summary == "found it"
        # The DIRECT turn's request is intentionally dropped on escalation
        # (direct never executes); only the ReAct turn's call runs.
        assert executor.calls == [("test.search", {"q": "y"})]

    async def test_direct_without_escalation_blocks(self) -> None:
        chat = ScriptedChat([fakes.calls_turn(("test.search", {"q": "x"}))])
        executor = DictExecutor({})
        runner, gate, _ = _harness(chat, executor, allow_escalation=False)
        agent = fakes.make_agent(mode=ExecutionMode.DIRECT)
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "policy"
        assert outcome.report.status.value == "blocked"
        assert executor.calls == []


class TestReActLoop:
    async def test_react_multiple_tools_then_report(self) -> None:
        chat = ScriptedChat(
            [
                fakes.calls_turn(("test.search", {"q": "a"})),
                fakes.calls_turn(("test.search", {"q": "b"})),
                fakes.report_turn(summary="both done", extra={"data": {"n": 2}}),
            ]
        )
        executor = DictExecutor(
            {"test.search": lambda args: fakes.ok_result("test.search", {"q": args["q"]})}
        )
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent()
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "success"
        assert [step.tool for step in outcome.trace.steps] == ["test.search", "test.search"]
        assert all(step.success for step in outcome.trace.steps)
        assert outcome.usage.tool_calls == 2
        assert outcome.usage.llm_calls == 3
        assert outcome.report.data == {"n": 2}
        assert "specialist.report" in chat.tool_schemas_seen[0]

    async def test_max_steps_stops(self) -> None:
        chat = ScriptedChat([fakes.calls_turn(("test.search", {"q": "a"}))], repeat_last=True)
        executor = DictExecutor({"test.search": lambda args: fakes.ok_result("test.search")})
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent()
        task = _task(budget=ExecutionBudget(max_react_steps=1, max_tool_calls=10))
        outcome = await runner.run(
            task, agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "max_steps"
        assert outcome.report.status.value == "blocked"

    async def test_no_progress_on_identical_consecutive_calls(self) -> None:
        chat = ScriptedChat([fakes.calls_turn(("test.search", {"q": "same"}))], repeat_last=True)
        executor = DictExecutor(
            {"test.search": lambda args: fakes.err_result("test.search", "empty")}
        )
        runner, gate, _ = _harness(chat, executor, repeat_remind_after=99, repeat_breaker_after=99)
        agent = fakes.make_agent()
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "no_progress"

    async def test_forbidden_tool_is_unavailable(self) -> None:
        chat = ScriptedChat(
            [
                fakes.calls_turn(("other.tool", {})),
                fakes.report_turn(summary="recovered"),
            ]
        )
        executor = DictExecutor({})
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent()
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.report.summary == "recovered"
        assert outcome.usage.tool_calls == 0
        assert "not available" in outcome.trace.steps[0].observation_summary

    async def test_tool_budget_exhausted(self) -> None:
        chat = ScriptedChat([fakes.calls_turn(("test.search", {}))])
        executor = DictExecutor({"test.search": lambda args: fakes.ok_result("test.search")})
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent()
        task = _task(budget=ExecutionBudget(max_react_steps=6, max_tool_calls=1))
        outcome = await runner.run(
            task, agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "max_tool_calls"

    async def test_llm_budget_exhausted(self) -> None:
        chat = ScriptedChat([fakes.calls_turn(("test.search", {}))])
        executor = DictExecutor({"test.search": lambda args: fakes.ok_result("test.search")})
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent()
        task = _task(budget=ExecutionBudget(max_llm_calls=0, max_tool_calls=10))
        outcome = await runner.run(
            task, agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "budget"

    async def test_timeout_stops(self) -> None:
        from app.domain.models import AssistantTurn, ChatMessage, ToolDefinition

        class SlowChat(ScriptedChat):
            async def complete(
                self, messages: list[ChatMessage], tools: list[ToolDefinition]
            ) -> AssistantTurn:
                await asyncio.sleep(0.2)
                return fakes.text_turn("late")

        chat = SlowChat([fakes.text_turn("late")])
        executor = DictExecutor({})
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent()
        task = _task(budget=ExecutionBudget(timeout_seconds=0.01))
        outcome = await runner.run(
            task, agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "timeout"
        assert outcome.report.status.value == "blocked"


class TestPolicyGating:
    async def test_mutation_blocked_in_read_only_view(self) -> None:
        chat = ScriptedChat([fakes.calls_turn(("test.write", {"x": 1}))], repeat_last=True)
        executor = DictExecutor({"test.write": lambda args: fakes.ok_result("test.write")})
        runner, gate, _ = _harness(
            chat, executor, tools=[fakes.make_read_tool(), fakes.make_mutation_tool()]
        )
        agent = fakes.make_agent()
        view = gate.for_agent(agent.name, read_only=True)
        assert "test.write" not in view.tool_names
        outcome = await runner.run(_task(), agent, view, run_id="r1", user_id="u1")
        # Least privilege: the runner cannot see past its view, so the gated
        # mutation surfaces as unavailable and is never executed.
        assert "not available" in outcome.trace.steps[0].observation_summary
        assert executor.calls == []

    async def test_mutation_rejected_when_permit_mutations_false(self) -> None:
        chat = ScriptedChat([fakes.calls_turn(("test.write", {"x": 1}))])
        executor = DictExecutor({"test.write": lambda args: fakes.ok_result("test.write")})
        runner, gate, _ = _harness(
            chat, executor, tools=[fakes.make_read_tool(), fakes.make_mutation_tool()]
        )
        agent = fakes.make_agent()
        outcome = await runner.run(
            _task(permit_mutations=False),
            agent,
            gate.for_agent(agent.name),
            run_id="r1",
            user_id="u1",
        )
        assert outcome.trace.stop_reason.value == "policy"
        assert outcome.report.status.value == "needs_approval"
        assert outcome.needs_approval is True
        assert executor.calls == []

    async def test_run_applies_task_tool_restriction_to_wide_view(self) -> None:
        chat = ScriptedChat(
            [fakes.calls_turn(("test.lookup", {"q": "x"}))],
            repeat_last=True,
        )
        executor = DictExecutor(
            {
                "test.search": lambda args: fakes.ok_result("test.search"),
                "test.lookup": lambda args: fakes.ok_result("test.lookup"),
            }
        )
        runner, gate, _ = _harness(
            chat,
            executor,
            tools=[fakes.make_read_tool(), fakes.make_read_tool("test.lookup")],
        )
        agent = fakes.make_agent()
        wide = gate.for_agent(agent.name)
        assert "test.lookup" in wide.tool_names
        outcome = await runner.run(
            _task(tool_restriction=ToolRestriction(allow=["test.search"])),
            agent,
            wide,
            run_id="r1",
            user_id="u1",
        )
        assert "not available" in outcome.trace.steps[0].observation_summary
        assert executor.calls == []


class TestPostReviewFixes:
    """Regression tests for the P11 review findings (H1/H2, M1/M2/M3/M5/M6)."""

    async def test_timeout_preserves_usage_and_trace(self) -> None:
        from app.domain.models import AssistantTurn, ChatMessage, ToolDefinition

        class FlakyChat(ScriptedChat):
            def __init__(self) -> None:
                super().__init__([fakes.calls_turn(("test.search", {"q": "x"}))])
                self._n = 0

            async def complete(
                self, messages: list[ChatMessage], tools: list[ToolDefinition]
            ) -> AssistantTurn:
                self._n += 1
                if self._n == 1:
                    return await super().complete(messages, tools)
                await asyncio.sleep(0.2)
                return fakes.text_turn("late")

        chat = FlakyChat()
        executor = DictExecutor(
            {"test.search": lambda args: fakes.ok_result("test.search", "data")}
        )
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent()
        task = _task(budget=ExecutionBudget(timeout_seconds=0.05))
        outcome = await runner.run(
            task, agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "timeout"
        assert outcome.usage.tool_calls == 1
        assert outcome.usage.llm_calls == 1
        assert len(outcome.trace.steps) == 1
        assert outcome.trace.steps[0].tool == "test.search"

    async def test_llm_budget_counts_exactly(self) -> None:
        script = [
            fakes.calls_turn(("test.search", {"q": "x"})),
            fakes.report_turn(summary="done"),
        ]
        executor = DictExecutor({"test.search": lambda args: fakes.ok_result("test.search")})
        runner, gate, _ = _harness(ScriptedChat(script), executor)
        agent = fakes.make_agent()
        ok_task = _task(budget=ExecutionBudget(max_llm_calls=2))
        ok_outcome = await runner.run(
            ok_task, agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert ok_outcome.trace.stop_reason.value == "success"
        assert ok_outcome.usage.llm_calls == 2

        runner2, gate2, _ = _harness(ScriptedChat(list(script)), executor)
        tight_task = _task(budget=ExecutionBudget(max_llm_calls=1))
        tight_outcome = await runner2.run(
            tight_task, agent, gate2.for_agent(agent.name), run_id="r2", user_id="u1"
        )
        assert tight_outcome.trace.stop_reason.value == "budget"
        assert tight_outcome.usage.llm_calls == 1
        assert tight_outcome.usage.tool_calls == 1

    async def test_executor_exception_feeds_guard_and_trips_breaker(self) -> None:
        from app.domain.models import ToolResult

        def _down(args: dict[str, Any]) -> ToolResult:
            del args
            raise RuntimeError("tool host down")

        chat = ScriptedChat([fakes.calls_turn(("test.search", {"q": "x"}))], repeat_last=True)
        executor = DictExecutor({"test.search": _down})
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent()
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        # Turn-level no-progress fires on the 2nd identical failing turn,
        # before the guard breaker (3) trips; the reminder was injected.
        assert outcome.trace.stop_reason.value == "no_progress"
        assert outcome.trace.circuit_broken is False
        assert len(executor.calls) == 2
        assert all(not step.success for step in outcome.trace.steps)
        assert outcome.trace.steps[1].reminder_injected is True

        # With no-progress detection off, the guard itself trips the breaker.
        chat2 = ScriptedChat([fakes.calls_turn(("test.search", {"q": "x"}))], repeat_last=True)
        executor2 = DictExecutor({"test.search": _down})
        runner2, gate2, _ = _harness(chat2, executor2, stop_on_no_progress=False)
        outcome2 = await runner2.run(
            _task(), agent, gate2.for_agent(agent.name), run_id="r2", user_id="u1"
        )
        assert outcome2.trace.stop_reason.value == "no_progress"
        assert outcome2.trace.circuit_broken is True
        assert len(executor2.calls) == 3

    async def test_token_budget_triggers_when_reported(self) -> None:
        from app.domain.models import AssistantTurn

        turn = AssistantTurn(
            text="",
            tool_calls=[],
            prompt_tokens=500,
            completion_tokens=10,
        )
        chat = ScriptedChat([turn])
        runner, gate, _ = _harness(chat, DictExecutor({}))
        agent = fakes.make_agent()
        task = _task(budget=ExecutionBudget(max_prompt_tokens=100))
        outcome = await runner.run(
            task, agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "budget"
        assert outcome.usage.prompt_tokens == 500

    async def test_oscillating_tools_detected_as_no_progress(self) -> None:
        tools = [fakes.make_read_tool("a.search"), fakes.make_read_tool("b.lookup")]
        chat = ScriptedChat(
            [
                fakes.calls_turn(("a.search", {"q": "1"})),
                fakes.calls_turn(("b.lookup", {"q": "2"})),
            ],
            repeat_last=True,
        )
        executor = DictExecutor(
            {
                "a.search": lambda args: fakes.ok_result("a.search", "a-data"),
                "b.lookup": lambda args: fakes.ok_result("b.lookup", "b-data"),
            }
        )
        runner, gate, _ = _harness(chat, executor, tools=tools)
        agent = fakes.make_agent()
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "no_progress"
        assert outcome.usage.llm_calls == 3
        assert outcome.usage.tool_calls == 3

    async def test_repeated_all_failed_turn_stops(self) -> None:
        chat = ScriptedChat(
            [fakes.calls_turn(("test.search", {"q": "a"}), ("test.search", {"q": "b"}))],
            repeat_last=True,
        )
        executor = DictExecutor(
            {"test.search": lambda args: fakes.err_result("test.search", "bad")}
        )
        runner, gate, _ = _harness(chat, executor)
        agent = fakes.make_agent()
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.trace.stop_reason.value == "no_progress"
        assert outcome.usage.llm_calls == 2
        assert outcome.usage.tool_calls == 4

    async def test_scripted_chat_raises_when_exhausted(self) -> None:
        import pytest as _pytest

        from app.domain.models import ChatMessage

        chat = ScriptedChat([fakes.text_turn("only")])
        await chat.complete([ChatMessage(role="user", content="hi")], [])
        with _pytest.raises(AssertionError, match="exhausted"):
            await chat.complete([ChatMessage(role="user", content="hi again")], [])


class TestTranscriptIntegrity:
    """Regression: non-terminal rejections must keep tool-call/tool-result pairing."""

    async def test_invalid_report_appends_tool_observation(self) -> None:
        chat = ScriptedChat(
            [
                fakes.calls_turn(("specialist.report", {"status": "bogus"})),
                fakes.report_turn(summary="recovered"),
            ]
        )
        runner, gate, _ = _harness(chat, DictExecutor({}))
        agent = fakes.make_agent()
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.report.summary == "recovered"
        # The follow-up prompt must carry a tool-role response for the rejected
        # call; a dangling tool_call breaks strict chat backends (P12 wiring).
        followup = chat.prompts[1]
        assert followup[-1].role == "tool"
        assert "Report rejected" in followup[-1].content

    async def test_unknown_tool_appends_tool_observation(self) -> None:
        chat = ScriptedChat(
            [
                fakes.calls_turn(("ghost.tool", {"x": 1})),
                fakes.report_turn(summary="done anyway"),
            ]
        )
        runner, gate, _ = _harness(chat, DictExecutor({}))
        agent = fakes.make_agent()
        outcome = await runner.run(
            _task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        assert outcome.report.summary == "done anyway"
        followup = chat.prompts[1]
        assert followup[-1].role == "tool"
        assert "not available" in followup[-1].content
        assert "ghost.tool" in followup[-1].content


class TestToolAdvertLeastPrivilege:
    """Regression: unusable mutation tools must not be advertised (P11-04)."""

    async def test_unusable_mutation_tools_not_advertised(self) -> None:
        tools = [fakes.make_read_tool(), fakes.make_mutation_tool()]
        chat = ScriptedChat([fakes.text_turn("ok")])
        runner, gate, _ = _harness(chat, DictExecutor({}), tools=tools)
        agent = fakes.make_agent()
        # Approvals off by default: the mutation tool can never execute, so it
        # must be invisible in the tool schemas sent to the LLM.
        await runner.run(_task(), agent, gate.for_agent(agent.name), run_id="r1", user_id="u1")
        assert "test.write" not in chat.tool_schemas_seen[0]
        assert "test.search" in chat.tool_schemas_seen[0]
        assert "specialist.report" in chat.tool_schemas_seen[0]

    async def test_permitted_mutation_tools_advertised(self) -> None:
        tools = [fakes.make_read_tool(), fakes.make_mutation_tool()]
        chat = ScriptedChat([fakes.text_turn("ok")])
        runner, gate, _ = _harness(chat, DictExecutor({}), tools=tools)
        agent = fakes.make_agent()
        token = generate_approval_token("appr-1", run_id="r1")
        task = _task(permit_mutations=True, approval_token=token)
        await runner.run(task, agent, gate.for_agent(agent.name), run_id="r1", user_id="u1")
        assert "test.write" in chat.tool_schemas_seen[0]

    async def test_invalid_mutation_token_downgrades_and_does_not_advertise_or_leak(self) -> None:
        """H2 & M2: Invalid token is not advertised, permit_mutations downgraded, token not leaked into prompts/trace."""
        tools = [fakes.make_read_tool(), fakes.make_mutation_tool()]
        chat = ScriptedChat([fakes.text_turn("ok")])
        runner, gate, _ = _harness(chat, DictExecutor({}), tools=tools)
        agent = fakes.make_agent()
        fake_secret_token = "appr_fake_forged_secret_token"
        task = _task(
            permit_mutations=True,
            approval_token=fake_secret_token,
            context_data={"approval_token": fake_secret_token, "note": "hello"},
        )
        outcome = await runner.run(
            task, agent, gate.for_agent(agent.name), run_id="r1", user_id="u1"
        )
        # M2: Mutation tool not advertised
        assert "test.write" not in chat.tool_schemas_seen[0]
        # H2: Token is not leaked in user prompt
        all_prompt_text = "\n".join(m.content for m in chat.prompts[0])
        assert fake_secret_token not in all_prompt_text
        assert "hello" in all_prompt_text
        # H2: Token is not in trace steps
        for step in outcome.trace.steps:
            assert fake_secret_token not in str(step.arguments)
