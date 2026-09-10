"""LangGraph interrupt and resume primitives (spec P18-03).

These helpers wrap interrupt() / Command(resume=...) and are confined to
app/harness/ per substrate isolation (§18A.5).

Production mutation HITL does not call interrupt_for_approval or resume_graph:
graphs persist via AsyncPostgresSaver, and approved writes resume through the
token API (POST /approvals/{id}/approve → appr_... → tool retry). These
functions are exercised by unit and Postgres checkpointer tests, and
interrupt_for_question is used by tool_ask_user when it runs inside a graph node.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import Command, interrupt


def interrupt_for_approval(
    approval_payload: dict[str, Any],
) -> dict[str, Any]:
    """Pause execution before executing an approval-required mutation action.

    LangGraph checkpoint saver persists the current state. Resuming provides
    the dictionary passed to Command(resume=...).
    """
    wrapped = {"type": "approval_required", **approval_payload}
    resumed_val = interrupt(wrapped)
    if isinstance(resumed_val, dict):
        return resumed_val
    return {"resumed": resumed_val}


def interrupt_for_question(
    question_payload: dict[str, Any],
) -> dict[str, Any]:
    """Pause execution awaiting user response to structured clarification questions."""
    wrapped = {"type": "question", **question_payload}
    resumed_val = interrupt(wrapped)
    if isinstance(resumed_val, dict):
        return resumed_val
    return {"resumed": resumed_val}


async def resume_graph(
    graph: Any,
    run_id: str,
    resume_payload: dict[str, Any],
) -> Any:
    """Resume an interrupted LangGraph run with the given resume payload.

    A pending approval must survive process restart and resume exactly once (spec P18-03).
    """
    config = {"configurable": {"thread_id": run_id}}
    cmd = Command(resume=resume_payload)
    return await graph.ainvoke(cmd, config=config)


__all__ = [
    "interrupt_for_approval",
    "interrupt_for_question",
    "resume_graph",
]
