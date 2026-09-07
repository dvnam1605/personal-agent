"""Bounded specialist execution: DIRECT path, ReAct loop, mode selector (P11-01..03/06).

First-party orchestration policy: stop conditions, budgets, capability gating,
and escalation rules live here — never in a prebuilt graph abstraction. The
only LLM touchpoint is the injected :class:`ChatBackend`; the only tool
touchpoint is the injected :class:`ToolExecutor`.

Progress accounting lives in :class:`_RunState`, owned by :meth:`run`, so a
timeout still returns the real usage/trace instead of empty counters.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from app.agents.specialist.guard import RepeatToolGuard, normalize_arguments
from app.agents.specialist.protocols import ChatBackend, ToolExecutor
from app.agents.specialist.report import (
    REPORT_TOOL_DEFINITION,
    REPORT_TOOL_NAME,
    ReportValidationError,
    coerce_report_arguments,
    parse_report_call,
    report_from_stop,
)
from app.core.sanitization import sanitize_payload
from app.domain.enums import ExecutionMode, SpecialistStatus, StopReason
from app.domain.errors import NotFoundError
from app.domain.models.agent import AgentDefinition
from app.domain.models.budget import BudgetUsage, evaluate_budget_violations
from app.domain.models.specialist import (
    AssistantTurn,
    ChatMessage,
    ReActTraceStep,
    SpecialistOutcome,
    SpecialistReport,
    SpecialistTask,
    SpecialistTrace,
    ToolCallRequest,
)
from app.domain.models.tool import ToolContext, ToolDefinition, ToolInput, ToolResult
from app.tools.registry import ToolRegistryView

logger = logging.getLogger(__name__)

# One turn signature: per-call (tool, canonical args, success) tuples.
TurnSignature = tuple[tuple[str, str, bool], ...]


@dataclass
class _RunState:
    """Mutable per-activation progress, owned by run() (timeout-safe)."""

    usage: BudgetUsage = field(default_factory=BudgetUsage)
    steps: list[ReActTraceStep] = field(default_factory=list)
    escalated: bool = False
    circuit_broken: bool = False
    turn_signature_counts: dict[TurnSignature, int] = field(default_factory=dict)


class ModeSelector:
    """Resolve DIRECT vs BOUNDED_REACT (spec P11-06)."""

    @staticmethod
    def select(task: SpecialistTask, agent: AgentDefinition) -> ExecutionMode:
        """Explicit task mode wins; otherwise the agent declaration default applies."""
        if task.mode is not None:
            return task.mode
        return agent.default_execution_mode


def _summarize_output(output: object, *, limit: int) -> str:
    """Render a tool output as a bounded observation string."""
    if output is None:
        return ""
    if isinstance(output, str):
        text = output
    else:
        try:
            text = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(output)
    text = text.strip()
    if len(text) > limit:
        return text[:limit] + "…"
    return text


class SpecialistRunner:
    """Execute one specialist activation under explicit bounds."""

    def __init__(
        self,
        chat: ChatBackend,
        executor: ToolExecutor,
        *,
        no_progress_limit: int = 2,
        stop_on_no_progress: bool = True,
        allow_escalation: bool = True,
        repeat_remind_after: int = 2,
        repeat_breaker_after: int = 3,
        observation_truncate: int = 500,
    ) -> None:
        if no_progress_limit < 1:
            raise ValueError("no_progress_limit must be >= 1")
        if repeat_remind_after < 1:
            raise ValueError("repeat_remind_after must be >= 1")
        if repeat_breaker_after < repeat_remind_after:
            raise ValueError("repeat_breaker_after must be >= repeat_remind_after")
        self._chat = chat
        self._executor = executor
        self._no_progress_limit = no_progress_limit
        self._stop_on_no_progress = stop_on_no_progress
        self._allow_escalation = allow_escalation
        self._repeat_remind_after = repeat_remind_after
        self._repeat_breaker_after = repeat_breaker_after
        self._observation_truncate = observation_truncate

    async def run(
        self,
        task: SpecialistTask,
        agent: AgentDefinition,
        tools: ToolRegistryView,
        *,
        run_id: str,
        user_id: str,
    ) -> SpecialistOutcome:
        """Run DIRECT or BOUNDED_REACT under the task budget (timeout enforced)."""
        state = _RunState()
        try:
            return await asyncio.wait_for(
                self._run_unbounded(task, agent, tools, state, run_id=run_id, user_id=user_id),
                timeout=task.budget.timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "specialist_timeout",
                extra={
                    "agent": agent.name,
                    "llm_calls": state.usage.llm_calls,
                    "tool_calls": state.usage.tool_calls,
                    "steps": len(state.steps),
                },
            )
            return self._finish(
                task,
                agent,
                state,
                StopReason.TIMEOUT,
                report_from_stop(StopReason.TIMEOUT, "Specialist execution timed out."),
            )

    async def _run_unbounded(
        self,
        task: SpecialistTask,
        agent: AgentDefinition,
        tools: ToolRegistryView,
        state: _RunState,
        *,
        run_id: str,
        user_id: str,
    ) -> SpecialistOutcome:
        mode = ModeSelector.select(task, agent)
        guard = RepeatToolGuard(
            remind_after=self._repeat_remind_after,
            breaker_after=self._repeat_breaker_after,
        )
        messages = self._initial_messages(task)
        # P11-04 least privilege: mutation tools this execution can never run
        # (read-only view or approvals off) are not advertised to the LLM.
        advertise_mutations = not tools.is_read_only and task.permit_mutations
        tool_schemas = [
            *(tool for tool in tools.list() if advertise_mutations or not tool.is_mutation),
            REPORT_TOOL_DEFINITION,
        ]
        token = task.approval_token or (
            task.context_data.get("approval_token")
            if isinstance(task.context_data, dict)
            else None
        )
        context = ToolContext(
            run_id=run_id,
            user_id=user_id,
            agent_name=agent.name,
            read_only_view=tools.is_read_only,
            approval_token=token,
        )
        iteration = 0

        if mode is ExecutionMode.DIRECT:
            direct_stop = self._pre_step_stop(task, state.usage)
            if direct_stop is not None:
                return self._finish(
                    task,
                    agent,
                    state,
                    direct_stop,
                    report_from_stop(direct_stop, self._stop_summary(direct_stop)),
                )
            turn = await self._complete(messages, tool_schemas, state)
            budget_stop = self._post_llm_stop(task, state.usage)
            if budget_stop is not None:
                return self._finish(
                    task,
                    agent,
                    state,
                    budget_stop,
                    report_from_stop(budget_stop, self._stop_summary(budget_stop)),
                )
            if not turn.tool_calls:
                return self._finish(
                    task,
                    agent,
                    state,
                    StopReason.SUCCESS,
                    self._report_from_text(turn.text),
                )
            if not self._allow_escalation:
                return self._finish(
                    task,
                    agent,
                    state,
                    StopReason.POLICY,
                    report_from_stop(StopReason.POLICY, "Direct mode does not execute tools."),
                )
            state.escalated = True
            messages.append(ChatMessage(role="assistant", content=turn.text or "Escalating."))
            logger.info("specialist_escalated_to_react", extra={"agent": agent.name})

        while True:
            iteration += 1
            stop = self._pre_step_stop(task, state.usage)
            if stop is not None:
                return self._finish(
                    task,
                    agent,
                    state,
                    stop,
                    report_from_stop(stop, self._stop_summary(stop)),
                )
            turn = await self._complete(messages, tool_schemas, state)
            budget_stop = self._post_llm_stop(task, state.usage)
            if budget_stop is not None:
                return self._finish(
                    task,
                    agent,
                    state,
                    budget_stop,
                    report_from_stop(budget_stop, self._stop_summary(budget_stop)),
                )
            state.usage.record_react_step()
            messages.append(ChatMessage(role="assistant", content=turn.text or "(tool calls)"))

            if not turn.tool_calls:
                return self._finish(
                    task,
                    agent,
                    state,
                    StopReason.SUCCESS,
                    self._report_from_text(turn.text),
                )

            turn_start = len(state.steps)
            for call in turn.tool_calls:
                outcome = await self._dispatch_call(
                    call, task, agent, tools, context, messages, state, guard, iteration
                )
                if outcome is not None:
                    report, stop_reason, _needs_approval = outcome
                    # _finish derives needs_approval from the report status,
                    # which is exactly what _dispatch_call returned.
                    return self._finish(task, agent, state, stop_reason, report)
            stalled = self._observe_turn_signature(state, turn_start)
            if stalled:
                return self._finish(
                    task,
                    agent,
                    state,
                    StopReason.NO_PROGRESS,
                    report_from_stop(
                        StopReason.NO_PROGRESS,
                        "No progress: repeated tool-call pattern detected.",
                    ),
                )

    # ------------------------------------------------------------------
    # Single tool-call dispatch: returns None to continue the loop.
    # ------------------------------------------------------------------

    async def _dispatch_call(
        self,
        call: ToolCallRequest,
        task: SpecialistTask,
        agent: AgentDefinition,
        tools: ToolRegistryView,
        context: ToolContext,
        messages: list[ChatMessage],
        state: _RunState,
        guard: RepeatToolGuard,
        iteration: int,
    ) -> tuple[SpecialistReport, StopReason, bool] | None:
        """Execute one call; non-None means the activation terminates."""
        if call.tool_name == REPORT_TOOL_NAME:
            try:
                report = parse_report_call(
                    call.model_copy(update={"arguments": coerce_report_arguments(call.arguments)})
                )
            except ReportValidationError as exc:
                observation = f"Report rejected: {exc}"
                self._record_step(
                    state, iteration, call, observation, success=False, reminder=False
                )
                # Keep the tool-call/tool-result pairing intact so real chat
                # backends never see a dangling tool_call in the transcript.
                messages.append(ChatMessage(role="tool", content=observation))
                return None
            return report, StopReason.SUCCESS, report.status is SpecialistStatus.NEEDS_APPROVAL

        try:
            definition = tools.get(call.tool_name)
        except NotFoundError:
            observation = f"Tool '{call.tool_name}' is not available to agent '{agent.name}'."
            self._record_step(
                state, iteration, call, observation, success=False, reminder=False
            )
            messages.append(ChatMessage(role="tool", content=observation))
            return None

        if definition.is_mutation and (tools.is_read_only or not task.permit_mutations):
            summary = (
                f"Blocked: tool '{call.tool_name}' requires approval, which is "
                "rejected automatically in this execution."
            )
            self._record_step(state, iteration, call, summary, success=False, reminder=False)
            status = (
                SpecialistStatus.NEEDS_APPROVAL
                if not task.permit_mutations
                else SpecialistStatus.BLOCKED
            )
            report = SpecialistReport(status=status, summary=summary, blockers=[summary])
            return report, StopReason.POLICY, status is SpecialistStatus.NEEDS_APPROVAL

        if state.usage.tool_calls >= task.budget.max_tool_calls:
            summary = self._stop_summary(StopReason.MAX_TOOL_CALLS)
            return (
                report_from_stop(StopReason.MAX_TOOL_CALLS, summary),
                StopReason.MAX_TOOL_CALLS,
                False,
            )

        started = time.perf_counter()
        result: ToolResult | None
        try:
            result = await self._executor.execute(
                ToolInput(tool_name=call.tool_name, arguments=call.arguments), context
            )
        except Exception as exc:  # noqa: BLE001 - executor failures become observations
            result = None
            executor_error = f"Tool executor error for '{call.tool_name}': {exc}"
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        state.usage.record_tool_call()

        if result is not None and result.success:
            observation = _summarize_output(result.output, limit=self._observation_truncate)
            guard_output: object = result.output
            guard_error: str | None = None
            success = True
        elif result is not None:
            observation = (result.error or "Unknown tool error").strip()
            guard_output, guard_error, success = None, observation, False
        else:
            observation = executor_error
            guard_output, guard_error, success = None, observation, False

        decision = guard.observe(
            call.tool_name,
            call.arguments,
            success=success,
            output=guard_output,
            error=guard_error,
        )
        if decision.reminder is not None:
            messages.append(ChatMessage(role="system", content=decision.reminder))
        messages.append(ChatMessage(role="tool", content=observation or "(empty)"))
        self._record_step(
            state,
            iteration,
            call,
            observation or "(empty)",
            success=success,
            reminder=decision.reminder is not None,
        )
        logger.debug(
            "specialist_tool_done",
            extra={"agent": agent.name, "tool": call.tool_name, "elapsed_ms": elapsed_ms},
        )
        if decision.tripped:
            state.circuit_broken = True
            summary = (
                f"Circuit breaker: tool '{call.tool_name}' repeated "
                f"{decision.repeat_count} unproductive calls."
            )
            return report_from_stop(StopReason.NO_PROGRESS, summary), StopReason.NO_PROGRESS, False
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _initial_messages(self, task: SpecialistTask) -> list[ChatMessage]:
        messages = [ChatMessage(role="system", content=text) for text in task.system_preamble]
        context_json = json.dumps(
            task.context_data, ensure_ascii=False, sort_keys=True, default=str
        )
        messages.append(
            ChatMessage(role="user", content=f"Goal: {task.goal}\nContext: {context_json}")
        )
        return messages

    async def _complete(
        self,
        messages: list[ChatMessage],
        tool_schemas: list[ToolDefinition],
        state: _RunState,
    ) -> AssistantTurn:
        turn = await self._chat.complete(messages, tool_schemas)
        state.usage.record_llm_call()
        if turn.prompt_tokens or turn.completion_tokens:
            state.usage.record_llm_metrics(
                prompt_tokens=turn.prompt_tokens,
                completion_tokens=turn.completion_tokens,
            )
        return turn

    def _pre_step_stop(self, task: SpecialistTask, usage: BudgetUsage) -> StopReason | None:
        if usage.llm_calls >= task.budget.max_llm_calls:
            return StopReason.BUDGET
        if usage.react_steps >= task.budget.max_react_steps:
            return StopReason.MAX_STEPS
        if usage.tool_calls >= task.budget.max_tool_calls:
            return StopReason.MAX_TOOL_CALLS
        return None

    def _post_llm_stop(self, task: SpecialistTask, usage: BudgetUsage) -> StopReason | None:
        # Call-count budgets are enforced pre-call so every paid response is
        # processed; here only token/cost violations (recorded per turn) stop.
        violations = evaluate_budget_violations(task.budget, usage)
        relevant = [
            violation
            for violation in violations
            if violation.resource_type
            in {"prompt_tokens", "completion_tokens", "total_tokens", "estimated_cost_usd"}
        ]
        if relevant:
            return StopReason.BUDGET
        return None

    def _observe_turn_signature(self, state: _RunState, turn_start: int) -> bool:
        """Track whole-turn call patterns; repeats (incl. oscillation) mean stall.

        Replaces last-step-only comparison, which missed multi-tool cycles like
        A,B,A,B. A repeated all-failed turn also trips here, bounding M1-style
        token burn without a terminal report. Note this check is stricter than
        :class:`RepeatToolGuard`: it also counts identical *successful* turns
        (deterministic duplicates add no information), whereas the guard itself
        resets on success — that is intentional, whole-turn burn is the thing
        being bounded here.
        """
        if not self._stop_on_no_progress:
            return False
        signature: TurnSignature = tuple(
            (step.tool, normalize_arguments(step.arguments), step.success)
            for step in state.steps[turn_start:]
        )
        if not signature:
            return False
        count = state.turn_signature_counts.get(signature, 0) + 1
        state.turn_signature_counts[signature] = count
        return count >= self._no_progress_limit

    @staticmethod
    def _stop_summary(stop: StopReason) -> str:
        return {
            StopReason.MAX_STEPS: "ReAct step budget exhausted.",
            StopReason.MAX_TOOL_CALLS: "Tool call budget exhausted.",
            StopReason.BUDGET: "Execution budget exhausted.",
            StopReason.TIMEOUT: "Specialist execution timed out.",
            StopReason.NO_PROGRESS: "No progress detected.",
            StopReason.POLICY: "Stopped by execution policy.",
            StopReason.SUCCESS: "Completed.",
        }[stop]

    def _report_from_text(self, text: str) -> SpecialistReport:
        summary = text.strip() or "Specialist completed without tool calls."
        return SpecialistReport(status=SpecialistStatus.SUCCESS, summary=summary)

    def _record_step(
        self,
        state: _RunState,
        iteration: int,
        call: ToolCallRequest,
        observation: str,
        *,
        success: bool,
        reminder: bool,
    ) -> None:
        sanitized = sanitize_payload(call.arguments)
        state.steps.append(
            ReActTraceStep(
                iteration=iteration,
                tool=call.tool_name,
                arguments=sanitized if isinstance(sanitized, dict) else {},
                observation_summary=observation[: self._observation_truncate],
                success=success,
                reminder_injected=reminder,
            )
        )

    @staticmethod
    def _depth_of(task: SpecialistTask) -> int:
        return task.delegation.delegation_depth if task.delegation else 0

    def _finish(
        self,
        task: SpecialistTask,
        agent: AgentDefinition,
        state: _RunState,
        stop: StopReason,
        report: SpecialistReport,
    ) -> SpecialistOutcome:
        return SpecialistOutcome(
            agent_name=agent.name,
            report=report,
            trace=SpecialistTrace(
                steps=state.steps,
                stop_reason=stop,
                escalated_to_react=state.escalated,
                circuit_broken=state.circuit_broken,
            ),
            usage=state.usage,
            needs_approval=report.status is SpecialistStatus.NEEDS_APPROVAL,
            delegation_depth=self._depth_of(task),
        )
