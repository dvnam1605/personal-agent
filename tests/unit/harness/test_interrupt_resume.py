"""Unit tests for LangGraph interrupt and resume mechanics (spec P18-03, §18A.4, §18A.6)."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.domain.errors import PermissionDeniedError
from app.harness.checkpointer import get_checkpointer_dsn
from app.harness.interrupts import interrupt_for_approval, resume_graph
from app.services.approvals import generate_approval_token, verify_approval_token
from app.services.approvals.consumed_store import InMemoryConsumedTokenStore


class InterruptGraphChannels(TypedDict):
    run_id: str
    action_type: str
    target: str
    parameters: dict[str, Any]
    approval_token: str | None
    execution_result: str | None


def test_checkpointer_dsn_derivation() -> None:
    asyncpg_url = "postgresql+asyncpg://user:pass@localhost:5434/assistant_db"
    psycopg_dsn = get_checkpointer_dsn(asyncpg_url)
    assert psycopg_dsn == "postgresql://user:pass@localhost:5434/assistant_db"
    assert "+asyncpg" not in psycopg_dsn


@pytest.mark.asyncio
async def test_interrupt_pause_and_durable_resume() -> None:
    """A mutation node pauses with interrupt(), survives restart, and resumes exactly once."""
    checkpointer = MemorySaver()

    def build_test_graph() -> Any:
        builder = StateGraph(InterruptGraphChannels)

        def check_policy_node(state: InterruptGraphChannels) -> dict[str, Any]:
            # Pause awaiting approval
            resume_data = interrupt_for_approval(
                {
                    "action_type": state["action_type"],
                    "target": state["target"],
                }
            )
            token = resume_data.get("token")
            return {"approval_token": token}

        def execute_mutation_node(state: InterruptGraphChannels) -> dict[str, Any]:
            token = state.get("approval_token")
            if not token:
                raise PermissionDeniedError("Cannot execute mutation without approval token.")
            return {"execution_result": f"Executed mutation on {state['target']}"}

        builder.add_node("check_policy", check_policy_node)
        builder.add_node("execute_mutation", execute_mutation_node)
        builder.set_entry_point("check_policy")
        builder.add_edge("check_policy", "execute_mutation")
        builder.add_edge("execute_mutation", END)
        return builder.compile(checkpointer=checkpointer)

    # 1. First process: Start run and hit interrupt
    graph_v1 = build_test_graph()
    thread_id = "run_test_interrupt_1"
    config = {"configurable": {"thread_id": thread_id}}

    init_state: InterruptGraphChannels = {
        "run_id": thread_id,
        "action_type": "send_email",
        "target": "target@example.com",
        "parameters": {"body": "hello"},
        "approval_token": None,
        "execution_result": None,
    }

    paused_result = await graph_v1.ainvoke(init_state, config=config)
    # Graph execution interrupted
    assert paused_result.get("execution_result") is None
    assert "__interrupt__" in paused_result

    # 2. Simulate process restart: create a completely new graph instance with the same checkpointer
    graph_v2 = build_test_graph()

    # User decision: mint single-use execution token
    token_store = InMemoryConsumedTokenStore()
    token = generate_approval_token(
        approval_id="appr_001",
        tool_name="gmail.send",
        run_id=thread_id,
    )

    # Resume graph run with resume payload
    resumed_result = await resume_graph(
        graph_v2,
        run_id=thread_id,
        resume_payload={"approved": True, "token": token},
    )

    assert resumed_result["approval_token"] == token
    assert resumed_result["execution_result"] == "Executed mutation on target@example.com"

    # Verify single-use consume safety
    consumed_ok = await verify_approval_token(
        token,
        tool_name="gmail.send",
        consume=True,
        store=token_store,
        expected_run_id=thread_id,
    )
    assert consumed_ok is True

    # Replay attempt fails: second consume rejected
    replay_ok = await verify_approval_token(
        token,
        tool_name="gmail.send",
        consume=True,
        store=token_store,
        expected_run_id=thread_id,
    )
    assert replay_ok is False
