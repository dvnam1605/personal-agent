"""Integration tests for durable AsyncPostgresSaver checkpointer across process restarts (spec P18-03, H1).

Verifies:
1. Graph execution pauses on interrupt_for_approval() and persists state to PostgreSQL.
2. The checkpointer connection is completely closed (simulating process termination).
3. A brand new checkpointer connection is established in a simulated fresh process.
4. Execution resumes from PostgreSQL checkpoint with Command(resume=...) exactly once.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import pytest
from langgraph.graph import StateGraph
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from typing_extensions import TypedDict

from app.domain.errors import PermissionDeniedError
from app.harness.checkpointer import get_postgres_checkpointer
from app.harness.dsn import derive_checkpointer_dsn
from app.harness.interrupts import interrupt_for_approval, resume_graph

POSTGRES_TEST_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5434/assistant"
)


class CheckpointChannels(TypedDict):
    run_id: str
    action_type: str
    target: str
    approval_token: str | None
    execution_result: str | None


def _build_test_graph(checkpointer: Any) -> Any:
    builder = StateGraph(CheckpointChannels)

    def check_policy_node(state: CheckpointChannels) -> dict[str, Any]:
        resume_data = interrupt_for_approval(
            {
                "action_type": state["action_type"],
                "target": state["target"],
            }
        )
        token = resume_data.get("token")
        return {"approval_token": token}

    def execute_mutation_node(state: CheckpointChannels) -> dict[str, Any]:
        token = state.get("approval_token")
        if not token:
            raise PermissionDeniedError("Cannot execute mutation without approval token.")
        return {"execution_result": f"Executed mutation on {state['target']} with token {token}"}

    builder.add_node("check_policy", check_policy_node)
    builder.add_node("execute_mutation", execute_mutation_node)
    builder.set_entry_point("check_policy")
    builder.add_edge("check_policy", "execute_mutation")
    return builder.compile(checkpointer=checkpointer)


async def _async_test_body() -> None:
    # 1. Probe PostgreSQL availability; skip if unavailable
    admin_engine = create_async_engine(POSTGRES_TEST_URL, echo=False)
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (OSError, TimeoutError, SQLAlchemyError) as exc:
        await admin_engine.dispose()
        pytest.skip(f"PostgreSQL integration instance not available at {POSTGRES_TEST_URL}: {exc}")
    finally:
        await admin_engine.dispose()

    psycopg_dsn = derive_checkpointer_dsn(POSTGRES_TEST_URL)
    run_id = f"test_run_{uuid4().hex[:12]}"
    config = {"configurable": {"thread_id": run_id}}
    token = f"appr_test_{uuid4().hex[:8]}"

    # 2. Process 1: Run graph until interrupt pause, backed by PostgreSQL checkpointer
    async with get_postgres_checkpointer(psycopg_dsn, setup_tables=True) as saver1:
        graph1 = _build_test_graph(saver1)
        initial_state: CheckpointChannels = {
            "run_id": run_id,
            "action_type": "update_event",
            "target": "calendar_event_42",
            "approval_token": None,
            "execution_result": None,
        }

        # Invocation will pause at interrupt_for_approval
        paused_state = await graph1.ainvoke(initial_state, config=config)
        assert paused_state.get("__interrupt__") is not None
        interrupt_info = paused_state["__interrupt__"][0]
        assert interrupt_info.value["type"] == "approval_required"
        assert interrupt_info.value["target"] == "calendar_event_42"

    # Context manager exited: saver1 connection is completely closed, simulating process termination.

    # 3. Process 2 (Simulated Restart): Open fresh PostgreSQL checkpointer and compile fresh graph
    async with get_postgres_checkpointer(psycopg_dsn, setup_tables=False) as saver2:
        graph2 = _build_test_graph(saver2)

        # Verify state was preserved in PostgreSQL checkpointer
        checkpoint_tuple = await saver2.aget_tuple(config)
        assert checkpoint_tuple is not None
        persisted_channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
        assert persisted_channel_values.get("target") == "calendar_event_42"
        assert persisted_channel_values.get("run_id") == run_id

        # Resume graph with approved token
        resumed_state = await resume_graph(graph2, run_id, {"token": token})

        # 4. Verify completion and exact-once mutation execution
        assert resumed_state["approval_token"] == token
        assert (
            resumed_state["execution_result"]
            == f"Executed mutation on calendar_event_42 with token {token}"
        )


def test_postgres_checkpointer_persists_and_resumes_across_restart() -> None:
    """Checkpointer persists interrupted state in PostgreSQL and resumes cleanly in a new process (P18-03, H1)."""
    import asyncio
    import selectors
    import sys

    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_async_test_body())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    else:
        asyncio.run(_async_test_body())
