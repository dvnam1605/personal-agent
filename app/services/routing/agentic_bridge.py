"""Run multi-domain requests through the agentic LangGraph supervisor.

The supervisor's LLM planner splits the request into tasks for specialist agents
(knowledge, calendar, communication). Each specialist runs a ReAct loop over live
read-only tools. This module runs that graph for one request and turns its outcome
into the ``QueryResult`` the chat API and the SSE stream already speak:

* completed / partial results are written up in Vietnamese by one LLM call, with
  every specialist output passed as untrusted data;
* a specialist that wants to write returns a ``proposed_action``. Only an email
  draft is accepted; it goes through the same recipient check and critic as a
  direct draft and becomes an approval request — nothing is written here;
* a run that needs more context asks the user;
* anything else raises ``AgenticRunError`` so the caller can fall back to the
  rule-based pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm import llm_text
from app.domain.enums import ActionRiskLevel
from app.domain.errors import AppError
from app.domain.models import ProposedAction
from app.services.approvals import ApprovalRequestService
from app.services.critic import DraftCritic
from app.services.retrieval.answer.injection_boundary import (
    BOUNDARY_INSTRUCTIONS,
    wrap_untrusted_tool_result,
)
from app.services.routing.query import QueryResult, QueryRouteInfo
from app.services.routing.query.mail.drafting import ask_recipients, prepare_draft

logger = logging.getLogger(__name__)

DRAFT_TOOL = "gmail.create_draft"
_SYNTHESIS_TIMEOUT_SECONDS = 45.0
_SUCCESS_STATUSES = {"completed", "partial_failure"}

SYNTHESIS_SYSTEM_PROMPT = (
    f"{BOUNDARY_INSTRUCTIONS}\n\n"
    "Bạn là trợ lý cá nhân. Viết câu trả lời cuối cùng bằng tiếng Việt cho yêu cầu của người "
    "dùng, chỉ dựa trên kết quả các bước đã thực hiện (trong các khối untrusted_tool_result). "
    "Trình bày gọn, có tiêu đề nhỏ cho từng phần nếu có nhiều phần. Không bịa thông tin; bước "
    "nào thất bại hoặc thiếu dữ liệu thì nói rõ."
)


class AgenticRunError(RuntimeError):
    """The agentic run produced no usable answer; the caller may fall back."""


GraphBuilderFn = Callable[[], Any]

_builder: Any | None = None


def _default_builder() -> Any:
    """Process-wide supervisor builder over the gateway and live read-only tools."""
    global _builder
    if _builder is None:
        from app.core.llm_tools import GatewayChatBackend
        from app.harness.runtime import (
            build_first_party_tool_registry,
            build_live_supervisor_graph_builder,
        )
        from app.harness.tool_executor import LiveToolExecutor, default_tool_factories
        from app.infrastructure.db.session import get_session_factory

        executor = LiveToolExecutor(
            build_first_party_tool_registry(), get_session_factory(), default_tool_factories()
        )
        _builder = build_live_supervisor_graph_builder(GatewayChatBackend(), executor)
    return _builder


async def run_agentic_supervisor(
    *,
    session: AsyncSession,
    user_id: str,
    query: str,
    run_id: str,
    route: QueryRouteInfo,
    personal: str = "",
    signature: str | None = None,
    critic: DraftCritic | None = None,
    builder: GraphBuilderFn | None = None,
    synthesize: Callable[[str, str], Any] | None = None,
) -> QueryResult:
    """Plan and run ``query`` with the supervisor graph and map its outcome."""
    graph = (builder or _default_builder)().build()
    channels = {
        "query": query,
        "goal": query,
        "user_id": user_id,
        "run_id": run_id,
        "status": "pending",
        "replan_count": 0,
    }
    try:
        async with asyncio.timeout(settings.agentic.dag_timeout_seconds):
            state = dict(
                await graph.ainvoke(channels, config={"configurable": {"thread_id": run_id}})
            )
    except TimeoutError as exc:
        raise AgenticRunError("agentic run timed out") from exc
    status = str(state.get("status") or "")
    outputs = _task_outputs(state)
    data = _run_data(state)
    logger.info("agentic_run_finished", extra={"run_id": run_id, "status": status})

    if status == "needs_approval":
        return await _approval_result(
            session=session,
            query=query,
            run_id=run_id,
            route=route,
            state=state,
            outputs=outputs,
            data=data,
            personal=personal,
            signature=signature,
            critic=critic,
        )
    if status == "needs_more_context":
        return _clarification_result(run_id, route, state, data)
    if status not in _SUCCESS_STATUSES or not outputs:
        raise AgenticRunError(f"agentic run ended with status '{status or 'unknown'}'")
    message = await (synthesize or _synthesize)(query, _evidence_block(outputs))
    return QueryResult(
        run_id=run_id,
        status="completed",
        message=message or _plain_summary(outputs),
        route=route,
        data=data,
    )


def _task_outputs(state: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(task name, status, output)`` for every task that produced text, in plan order."""
    plan = state.get("plan")
    names = {task.id: task.name for task in getattr(plan, "tasks", []) or []}
    results = state.get("task_results") or {}
    out: list[tuple[str, str, str]] = []
    for task_id, result in results.items():
        if not isinstance(result, dict):
            continue
        text = str(result.get("output") or "").strip()
        if text:
            out.append((names.get(task_id, task_id), str(result.get("status") or ""), text))
    return out


def _run_data(state: dict[str, Any]) -> dict[str, Any]:
    plan = state.get("plan")
    results = state.get("task_results") or {}
    return {
        "engine": "agentic",
        "plan": [
            {
                "id": task.id,
                "name": task.name,
                "agent": task.assigned_agent,
                "status": str((results.get(task.id) or {}).get("status") or "pending"),
            }
            for task in getattr(plan, "tasks", []) or []
        ],
        "citations": [],
    }


def _evidence_block(outputs: list[tuple[str, str, str]]) -> str:
    return "\n\n".join(
        wrap_untrusted_tool_result(f"{name} ({status})", text) for name, status, text in outputs
    )


def _plain_summary(outputs: list[tuple[str, str, str]]) -> str:
    return "\n\n".join(f"**{name}**\n{text}" for name, _status, text in outputs)


async def _synthesize(query: str, evidence: str) -> str | None:
    return await llm_text(
        SYNTHESIS_SYSTEM_PROMPT,
        f"Yêu cầu của người dùng: {query}\n\nKết quả các bước:\n{evidence}",
        timeout=_SYNTHESIS_TIMEOUT_SECONDS,
        temperature=0.2,
    )


def _clarification_result(
    run_id: str, route: QueryRouteInfo, state: dict[str, Any], data: dict[str, Any]
) -> QueryResult:
    missing = [str(item) for item in state.get("missing_context") or [] if str(item).strip()]
    questions = list(dict.fromkeys(missing)) or ["Bạn có thể nói rõ hơn yêu cầu không?"]
    lines = ["Tôi cần thêm thông tin để làm tiếp:", *(f"- {item}" for item in questions)]
    return QueryResult(
        run_id=run_id,
        status="clarification_needed",
        message="\n".join(lines),
        route=route,
        data=data,
    )


def _proposed_draft(state: dict[str, Any]) -> dict[str, Any] | None:
    """The first proposed email draft, or None when no acceptable proposal exists."""
    for entry in state.get("needs_approval") or []:
        proposal = entry.get("proposed_action") if isinstance(entry, dict) else None
        if isinstance(proposal, dict) and proposal.get("tool_name") == DRAFT_TOOL:
            arguments = proposal.get("arguments")
            if isinstance(arguments, dict):
                return arguments
    return None


def _recipients(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


async def _approval_result(
    *,
    session: AsyncSession,
    query: str,
    run_id: str,
    route: QueryRouteInfo,
    state: dict[str, Any],
    outputs: list[tuple[str, str, str]],
    data: dict[str, Any],
    personal: str,
    signature: str | None,
    critic: DraftCritic | None,
) -> QueryResult:
    """Turn a proposed email draft into an approval request after the usual checks."""
    arguments = _proposed_draft(state)
    if arguments is None:
        raise AgenticRunError("agentic run proposed an action that is not an email draft")
    subject = str(arguments.get("subject") or "").strip()
    body = str(arguments.get("body_text") or arguments.get("body") or "").strip()
    if not subject or not body:
        raise AgenticRunError("proposed email draft is missing a subject or body")
    context = "\n\n".join(text for _name, _status, text in outputs)
    plan = await prepare_draft(
        query,
        subject,
        body,
        _recipients(arguments.get("to")),
        personal=personal,
        context=context,
        signature=signature,
        critic=critic,
    )
    if plan.needs_recipients:
        return ask_recipients(run_id, route, query, subject)
    to_list = list(plan.draft.recipients)
    proposal = ProposedAction(
        action_type="create_draft",
        description=f"Tạo bản nháp email '{plan.draft.subject}' tới {', '.join(to_list)} trên Gmail",
        tool_name=DRAFT_TOOL,
        parameters={"to": to_list, "subject": plan.draft.subject, "body_text": plan.draft.body},
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        requires_approval=True,
    )
    try:
        approval = await ApprovalRequestService.create_request(session, run_id, proposal)
    except AppError as exc:
        raise AgenticRunError("could not create the approval request") from exc
    findings = _plain_summary([o for o in outputs if o[1] != "needs_approval"])
    message = "\n".join(
        [
            *([findings, ""] if findings else []),
            "#### ✉️ Bản nháp email",
            f"📌 **Tiêu đề:** {plan.draft.subject}  ",
            f"📬 **Người nhận:** {', '.join(to_list)}  ",
            "",
            "---",
            "",
            plan.draft.body,
            "",
            "---",
            "",
            "> 💡 *Nhấn **Duyệt** để tạo bản nháp này trên Gmail của bạn.*",
        ]
    )
    return QueryResult(
        run_id=run_id,
        status="needs_approval",
        message=message,
        route=route,
        data={
            **data,
            "proposal": proposal.model_dump(mode="json"),
            "subject": plan.draft.subject,
            "body": plan.draft.body,
            "to": to_list,
        },
        approval_id=approval.id,
    )


__all__ = ["AgenticRunError", "run_agentic_supervisor"]
