"""Supervisor StateGraph compiled on the LangGraph substrate (spec P16 / §18A.5 / ADR 0011).

Topology:
1. plan_node: Generates the initial typed ExecutionPlan.
2. validate_node: Validates DAG structure, cycles, budget, and depth.
3. step_barrier: Evaluates ready tasks; dispatches independent tasks in parallel via Send API.
4. execute_specialist_task: Executes one specialist task (concurrency supported via Send).
5. evaluate_replan: Bounded replanning when tasks report missing context; detects no-progress loops.
6. synthesize_result: Final grounded synthesis with evidence citations and approval aggregation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, cast

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from app.domain.enums import SpecialistStatus, TaskStatus
from app.domain.errors import AppTimeoutError, BudgetExceededError, ConfigurationError
from app.domain.models.platform.budget import ExecutionBudget
from app.domain.models.platform.tool import ToolRestriction
from app.domain.models.supervisor import CapabilityCatalog
from app.domain.models.supervisor.plan import ExecutionPlan
from app.harness.supervisor.channels import SupervisorChannels, TaskDispatchChannel
from app.services.supervisor.planner import SupervisorPlanner
from app.services.supervisor.session_manager import ContinuableSessionManager
from app.services.supervisor.validator import validate_execution_plan

logger = logging.getLogger(__name__)

TaskExecutor = Callable[[TaskDispatchChannel], Coroutine[Any, Any, dict[str, Any]]]

_BUDGET_ERRORS = (AppTimeoutError, BudgetExceededError)
_FAILED_STATUSES = frozenset({"failed", "error", "blocked"})
_SUCCESS_STATUSES = frozenset({"completed", "success"})


def _halt_from_budget(exc: BaseException) -> dict[str, Any]:
    return {
        "status": "failed",
        "branch_errors": [str(exc)],
        "final_synthesis": f"Supervisor stopped: {exc}",
    }


class SupervisorGraphBuilder:
    """Compiles the dynamic Supervisor DAG execution substrate on LangGraph."""

    def __init__(
        self,
        planner: SupervisorPlanner,
        catalog: CapabilityCatalog,
        budget: ExecutionBudget | None = None,
        *,
        task_executor: TaskExecutor | None = None,
        session_manager: ContinuableSessionManager | None = None,
        max_replans: int = 2,
        budget_manager: Any | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._planner = planner
        self._catalog = catalog
        self._budget = budget or ExecutionBudget()
        self._task_executor = task_executor
        self._session_manager = session_manager or ContinuableSessionManager()
        self._max_replans = max_replans
        self._budget_manager = budget_manager
        self._timeout_seconds = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(self._budget.timeout_seconds)
        )
        self._previous_plans: list[ExecutionPlan] = []
        self._active_sessions: dict[tuple[str, str], str] = {}

    def compile(self, *, checkpointer: Any | None = None) -> Any:
        """LangGraph-shaped alias for :meth:`build`."""
        return self.build(checkpointer=checkpointer)

    def build(self, *, checkpointer: Any | None = None) -> Any:
        """Assemble and compile the Supervisor StateGraph."""

        async def _plan_node(state: SupervisorChannels) -> dict[str, Any]:
            try:
                if self._budget_manager is not None:
                    self._budget_manager.check_deadline()
                    await self._budget_manager.record_supervisor_iteration(1)

                query = state.get("query", "")
                goal = state.get("goal") or query
                plan = state.get("plan")
                if plan is not None:
                    # Resume: keep completed_task_ids / evidence / missing_context.
                    return {"plan": plan, "status": "planning"}

                plan = await self._planner.plan(
                    query=query,
                    goal=goal,
                    catalog=self._catalog,
                    budget=self._budget,
                )
                self._previous_plans.append(plan)
                return {
                    "plan": plan,
                    "status": "planning",
                    "replan_count": 0,
                    "completed_task_ids": [],
                    "branch_errors": [],
                    "needs_approval": [],
                    "missing_context": [],
                    "handled_missing_context": [],
                    "handled_missing_task_ids": [],
                    "evidence": [],
                    "task_results": {},
                }
            except _BUDGET_ERRORS as exc:
                return _halt_from_budget(exc)

        def _route_after_plan(state: SupervisorChannels) -> str:
            if state.get("status") == "failed":
                return "synthesize_result"
            return "validate_node"

        def _validate_node(state: SupervisorChannels) -> dict[str, Any]:
            plan = state.get("plan")
            if plan is None:
                return {
                    "status": "validation_failed",
                    "branch_errors": ["No plan available to validate."],
                    "final_synthesis": "Plan validation failed: No plan available.",
                }

            val_res = validate_execution_plan(plan, self._catalog, self._budget)
            if not val_res.is_valid:
                err_msg = f"Plan validation failed: {'; '.join(val_res.errors)}"
                logger.warning("supervisor_dag_validation_failed", extra={"errors": val_res.errors})
                return {
                    "status": "validation_failed",
                    "branch_errors": list(val_res.errors),
                    "final_synthesis": err_msg,
                }

            return {"status": "validated"}

        def _route_after_validate(state: SupervisorChannels) -> str:
            if state.get("status") == "validation_failed":
                return "synthesize_result"
            return "step_barrier"

        def _step_barrier(state: SupervisorChannels) -> dict[str, Any]:
            # Synchronization barrier: returns state pass-through
            return {"status": "in_progress"}

        def _route_step_barrier(state: SupervisorChannels) -> list[Send] | str:
            plan = state.get("plan")
            if plan is None:
                return "synthesize_result"

            if self._budget_manager is not None:
                try:
                    self._budget_manager.check_deadline()
                except _BUDGET_ERRORS:
                    return "synthesize_result"

            completed_ids = set(state.get("completed_task_ids", []))
            task_results = state.get("task_results", {})

            ready_tasks = plan.get_ready_tasks(context=task_results, completed_ids=completed_ids)
            pending_ready = [
                t
                for t in ready_tasks
                if t.id not in completed_ids
                and (
                    t.id not in task_results
                    or _status_value(task_results[t.id].get("status")) == "replan_pending"
                )
            ]

            if pending_ready:
                sends: list[Send] = []
                query = state.get("query", "")
                user_id = state.get("user_id", "")
                run_id = state.get("run_id", "")

                task_depths = plan.calculate_task_depths()
                for task in pending_ready:
                    tool_restriction, restriction_error = _restriction_from_input(task.input_data)
                    payload: TaskDispatchChannel = {
                        "task_id": task.id,
                        "task_name": task.name,
                        "assigned_agent": task.assigned_agent,
                        "description": task.description,
                        "input_data": task.input_data,
                        "query": query,
                        "user_id": user_id,
                        "run_id": run_id,
                        "depth": task_depths.get(task.id, 1),
                        "tool_restriction": tool_restriction,
                        "context_data": dict(task_results),
                    }
                    if restriction_error:
                        payload["restriction_error"] = restriction_error
                    sends.append(Send("execute_specialist_task", payload))
                return sends

            all_done = all(t.id in completed_ids for t in plan.tasks)
            handled_ids = set(state.get("handled_missing_task_ids", []))
            needs_more_ids = _needs_more_task_ids(task_results, handled_ids)
            replan_count = state.get("replan_count", 0)

            if needs_more_ids and replan_count < self._max_replans:
                return "evaluate_replan"

            if all_done or not pending_ready:
                return "synthesize_result"

            return "synthesize_result"

        async def _execute_specialist_task(payload: TaskDispatchChannel) -> dict[str, Any]:
            task_id = payload["task_id"]
            agent_name = payload["assigned_agent"]
            logger.info(
                "executing_supervisor_task", extra={"task_id": task_id, "agent": agent_name}
            )

            restriction_error = payload.get("restriction_error")
            if restriction_error:
                err = f"Task {task_id} rejected: {restriction_error}"
                return {
                    "task_results": {task_id: {"error": err, "status": "failed"}},
                    "branch_errors": [err],
                }

            if self._task_executor is None:
                err = (
                    "SupervisorGraphBuilder requires a task_executor. "
                    "Inject a DelegationService-backed executor; refusing to fabricate evidence."
                )
                logger.error("supervisor_task_executor_unwired", extra={"task_id": task_id})
                return {
                    "task_results": {task_id: {"error": err, "status": "failed"}},
                    "branch_errors": [err],
                }

            session_id: str | None = None
            user_id = str(payload.get("user_id") or "").strip()
            run_id = str(payload.get("run_id") or "").strip()
            if user_id and run_id:
                session_key = (agent_name, run_id)
                existing_sid = self._active_sessions.get(session_key)
                if existing_sid and self._session_manager.get_session(existing_sid):
                    session_id = existing_sid
                else:
                    try:
                        session = self._session_manager.create_session(
                            agent_name=agent_name,
                            user_id=user_id,
                            parent_run_id=run_id,
                            depth=int(payload.get("depth") or 0),
                            tool_restriction=payload.get("tool_restriction"),
                            context_data={"task_id": task_id},
                        )
                        session_id = session.session_id
                        self._active_sessions[session_key] = session_id
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "supervisor_session_create_failed", extra={"task_id": task_id}
                        )
                        session_id = None

                if session_id:
                    payload["session_id"] = session_id
                    try:
                        self._session_manager.append_message(
                            session_id, "user", payload.get("description") or task_id
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("supervisor_session_append_failed: %s", exc)

            try:
                if self._budget_manager is not None:
                    self._budget_manager.check_deadline()
                # Give the inner specialist timeout a chance to return usage (M5)
                # before this outer wait_for cancels the coroutine.
                raw = await asyncio.wait_for(
                    self._task_executor(payload),
                    timeout=self._timeout_seconds + 1.0,
                )
                normalized = _normalize_executor_result(task_id, raw)
                if session_id:
                    tdata = (normalized.get("task_results") or {}).get(task_id) or {}
                    summary = str(tdata.get("output") or tdata.get("status") or "done")[:4000]
                    try:
                        self._session_manager.append_message(
                            session_id, "assistant", summary or "done"
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "supervisor_session_append_failed", extra={"task_id": task_id}
                        )
                return normalized
            except TimeoutError:
                err = f"Task {task_id} timed out after {self._timeout_seconds:.1f}s"
                usage: dict[str, Any] = {}
                if self._budget_manager is not None:
                    self._budget_manager.usage.elapsed_seconds = (
                        self._budget_manager.elapsed_seconds
                    )
                    usage = self._budget_manager.usage.model_dump()
                return {
                    "task_results": {task_id: {"error": err, "status": "failed"}},
                    "branch_errors": [err],
                    "usage": usage,
                }
            except _BUDGET_ERRORS as exc:
                err = f"Task {task_id} failed: {exc}"
                return {
                    "task_results": {task_id: {"error": str(exc), "status": "failed"}},
                    "branch_errors": [err],
                }
            except ConfigurationError as exc:
                logger.error(
                    "task_executor_config_error", extra={"task_id": task_id, "error": str(exc)}
                )
                return {
                    "task_results": {task_id: {"error": str(exc), "status": "failed"}},
                    "branch_errors": [f"Task {task_id} failed: {exc}"],
                }
            except Exception as exc:  # noqa: BLE001
                logger.error("task_executor_error", extra={"task_id": task_id, "error": str(exc)})
                return {
                    "task_results": {task_id: {"error": str(exc), "status": "failed"}},
                    "branch_errors": [f"Task {task_id} failed: {exc}"],
                }

        async def _evaluate_replan(state: SupervisorChannels) -> dict[str, Any]:
            try:
                if self._budget_manager is not None:
                    self._budget_manager.check_deadline()
                    await self._budget_manager.record_supervisor_iteration(1)

                replan_count = state.get("replan_count", 0)
                missing = state.get("missing_context", [])
                handled_ids = set(state.get("handled_missing_task_ids", []))
                task_results = state.get("task_results", {})
                needs_more_ids = _needs_more_task_ids(task_results, handled_ids)
                plan = state.get("plan")

                if not plan or not needs_more_ids or replan_count >= self._max_replans:
                    return {"status": "replan_skipped"}

                completed_ids = set(state.get("completed_task_ids", []))
                completed_tasks = [
                    t
                    for t in plan.tasks
                    if t.id in completed_ids
                    and _status_value((task_results.get(t.id) or {}).get("status"))
                    not in _FAILED_STATUSES
                    and _status_value((task_results.get(t.id) or {}).get("status"))
                    not in (
                        TaskStatus.NEEDS_MORE_CONTEXT.value,
                        SpecialistStatus.NEEDS_MORE_CONTEXT.value,
                    )
                ]

                logger.info(
                    "supervisor_evaluating_replan",
                    extra={"replan_count": replan_count, "unhandled_tasks": needs_more_ids},
                )

                new_plan = await self._planner.replan(
                    original_plan=plan,
                    completed_tasks=completed_tasks,
                    missing_context=missing,
                    catalog=self._catalog,
                    budget=self._budget,
                )

                # Check no-progress loop (P16-07)
                if self._planner.detect_no_progress(self._previous_plans, new_plan):
                    logger.warning("supervisor_replan_no_progress_halt")
                    return {
                        "status": "no_progress_halted",
                        "branch_errors": ["No progress detected during plan refinement."],
                    }

                self._previous_plans.append(new_plan)
                return {
                    "plan": new_plan,
                    "replan_count": replan_count + 1,
                    "handled_missing_task_ids": list(needs_more_ids),
                    "handled_missing_context": list(missing),
                    "task_results": {tid: {"status": "replan_pending"} for tid in needs_more_ids},
                    "status": "replanned",
                }
            except _BUDGET_ERRORS as exc:
                return _halt_from_budget(exc)

        def _route_after_replan(state: SupervisorChannels) -> str:
            if state.get("status") in ("no_progress_halted", "replan_skipped", "failed"):
                return "synthesize_result"
            return "step_barrier"

        def _synthesize_result(state: SupervisorChannels) -> dict[str, Any]:
            return self.synthesize_result(state)

        # Build LangGraph StateGraph
        builder: StateGraph[SupervisorChannels, Any, Any, Any] = StateGraph(SupervisorChannels)
        builder.add_node("plan_node", _plan_node)
        builder.add_node("validate_node", _validate_node)
        builder.add_node("step_barrier", _step_barrier)
        builder.add_node("execute_specialist_task", cast(Any, _execute_specialist_task))
        builder.add_node("evaluate_replan", _evaluate_replan)
        builder.add_node("synthesize_result", _synthesize_result)

        builder.set_entry_point("plan_node")
        builder.add_conditional_edges(
            "plan_node",
            _route_after_plan,
            ["validate_node", "synthesize_result"],
        )
        builder.add_conditional_edges(
            "validate_node",
            _route_after_validate,
            ["step_barrier", "synthesize_result"],
        )
        builder.add_conditional_edges(
            "step_barrier",
            _route_step_barrier,
            ["execute_specialist_task", "evaluate_replan", "synthesize_result"],
        )
        builder.add_edge("execute_specialist_task", "step_barrier")
        builder.add_conditional_edges(
            "evaluate_replan",
            _route_after_replan,
            ["step_barrier", "synthesize_result"],
        )
        builder.add_edge("synthesize_result", END)

        if checkpointer is not None:
            return builder.compile(checkpointer=checkpointer)
        return builder.compile()

    def synthesize_result(self, state: SupervisorChannels) -> dict[str, Any]:
        """Produce the final grounded synthesis and aggregate approval requirements."""
        plan = state.get("plan")
        task_results = state.get("task_results", {})
        evidence = state.get("evidence", [])
        branch_errors = state.get("branch_errors", [])
        needs_approval = state.get("needs_approval", [])
        curr_status = state.get("status")

        failed_like = _has_failed_task(task_results)
        successful = _has_successful_task(task_results)

        if curr_status == "validation_failed":
            final_status = "validation_failed"
        elif needs_approval:
            final_status = "needs_approval"
        elif curr_status == "no_progress_halted":
            final_status = "no_progress_halted"
        elif _has_unresolved_needs_more(task_results, state.get("handled_missing_task_ids", [])):
            final_status = "needs_more_context"
        elif (branch_errors or failed_like) and successful:
            final_status = "partial_failure"
        elif branch_errors or failed_like:
            final_status = "failed"
        else:
            final_status = "completed"

        lines: list[str] = [f"Supervisor Execution Plan Summary for: '{state.get('query', '')}'"]
        if plan:
            lines.append(f"Goal: {plan.goal}")
        lines.append(f"Status: {final_status}")
        lines.append(f"Completed Tasks: {len(task_results)}")
        for tid, tdata in task_results.items():
            out = tdata.get("output", "") if isinstance(tdata, dict) else str(tdata)
            lines.append(f"- Task [{tid}]: {out}")

        if evidence:
            lines.append(f"Evidence Collected: {len(evidence)} items.")

        if needs_approval:
            lines.append(
                f"Pending Approvals: {len(needs_approval)} action(s) require authorization."
            )

        if branch_errors:
            lines.append(f"Errors/Warnings: {'; '.join(branch_errors)}")

        synthesis = "\n".join(lines)
        curr_run_id = str(state.get("run_id") or "").strip()
        keys_to_clean = [
            k for k in list(self._active_sessions.keys()) if not curr_run_id or k[1] == curr_run_id
        ]
        for k in keys_to_clean:
            s_id = self._active_sessions.pop(k, None)
            if s_id:
                try:
                    self._session_manager.close_session(s_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("supervisor_session_close_failed: %s", exc)

        if len(self._active_sessions) > 500:
            oldest_keys = list(self._active_sessions.keys())[:100]
            for k in oldest_keys:
                old_sid = self._active_sessions.pop(k, None)
                if old_sid:
                    try:
                        self._session_manager.close_session(old_sid)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("supervisor_old_session_close_failed: %s", exc)

        return {
            "final_synthesis": synthesis,
            "status": final_status,
        }


def _restriction_from_input(
    input_data: dict[str, Any],
) -> tuple[ToolRestriction | None, str | None]:
    if "allowed_tools" not in input_data and "denied_tools" not in input_data:
        return None, None
    allow = input_data.get("allowed_tools")
    deny = input_data.get("denied_tools")
    if allow is not None and not isinstance(allow, list):
        return None, "invalid allowed_tools: expected a list"
    if deny is not None and not isinstance(deny, list):
        return None, "invalid denied_tools: expected a list"
    try:
        return ToolRestriction(allow=allow, deny=deny), None
    except Exception as exc:  # noqa: BLE001
        logger.warning("supervisor_tool_restriction_rejected", extra={"error": str(exc)})
        return None, f"invalid tool restriction: {exc}"


def _status_value(value: Any) -> str:
    if isinstance(value, TaskStatus):
        return value.value
    if isinstance(value, SpecialistStatus):
        return value.value
    return str(value or "")


def _needs_more_task_ids(task_results: dict[str, Any], handled_ids: set[str]) -> list[str]:
    ids: list[str] = []
    for tid, tdata in task_results.items():
        if tid in handled_ids or not isinstance(tdata, dict):
            continue
        if _status_value(tdata.get("status")) in (
            TaskStatus.NEEDS_MORE_CONTEXT.value,
            SpecialistStatus.NEEDS_MORE_CONTEXT.value,
        ):
            ids.append(tid)
    return ids


def _has_unresolved_needs_more(
    task_results: dict[str, Any], handled_ids: list[str] | set[str]
) -> bool:
    return bool(_needs_more_task_ids(task_results, set(handled_ids)))


def _has_failed_task(task_results: dict[str, Any]) -> bool:
    for tdata in task_results.values():
        if not isinstance(tdata, dict):
            continue
        if _status_value(tdata.get("status")) in _FAILED_STATUSES:
            return True
    return False


def _has_successful_task(task_results: dict[str, Any]) -> bool:
    for tdata in task_results.values():
        if not isinstance(tdata, dict):
            continue
        if _status_value(tdata.get("status")) in _SUCCESS_STATUSES:
            return True
        if tdata.get("output") and _status_value(tdata.get("status")) not in _FAILED_STATUSES:
            return True
    return False


def _normalize_executor_result(task_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Lift NEEDS_* handshake fields onto supervisor channels."""
    out = dict(result)
    results = out.get("task_results") or {}
    tdata = results.get(task_id) if isinstance(results, dict) else None
    status = _status_value(tdata.get("status") if isinstance(tdata, dict) else None)

    if status in (
        TaskStatus.NEEDS_MORE_CONTEXT.value,
        SpecialistStatus.NEEDS_MORE_CONTEXT.value,
    ) and not out.get("missing_context"):
        needed: list[str] = []
        if isinstance(tdata, dict):
            extra = tdata.get("missing_context") or tdata.get("what_is_needed")
            if isinstance(extra, str) and extra.strip():
                needed = [extra]
            elif isinstance(extra, list):
                needed = [str(item) for item in extra if item]
        out["missing_context"] = needed or [f"Task {task_id} needs more context"]

    if status in (
        TaskStatus.NEEDS_APPROVAL.value,
        SpecialistStatus.NEEDS_APPROVAL.value,
    ) and not out.get("needs_approval"):
        out["needs_approval"] = [{"task_id": task_id, "status": status}]
    return out
