"""Unit tests for the LangGraph harness boundary (spec P11-00, MASTER_PLAN §18A)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.agents import AgentRegistry
from app.agents.specialist.react import SpecialistRunner
from app.domain.enums import ExecutionMode
from app.domain.errors import ConfigurationError
from app.domain.models import AssistantState, ExecutionBudget, SpecialistTask
from app.harness import (
    SpecialistGraphBuilder,
    apply_channels_to_state,
    derive_checkpointer_dsn,
    state_to_channels,
)
from app.services.capability_gate import CapabilityGate
from app.tools.registry import ToolRegistry
from tests.unit.agents import _fakes as fakes
from tests.unit.agents._fakes import DictExecutor, ScriptedChat


def _stack(chat: ScriptedChat, executor: DictExecutor) -> tuple[SpecialistGraphBuilder, str, str]:
    agents = AgentRegistry(
        [
            fakes.make_agent("Direct", mode=ExecutionMode.DIRECT),
            fakes.make_agent("Reacter", mode=ExecutionMode.BOUNDED_REACT),
        ]
    )
    tools = ToolRegistry([fakes.make_read_tool()])
    gate = CapabilityGate(tools, agents)
    builder = SpecialistGraphBuilder(SpecialistRunner(chat, executor), agents, gate)
    return builder, "r-harness", "u-harness"


class TestChannelConversion:
    def test_round_trip_preserves_fields_and_accumulates(self) -> None:
        state = AssistantState(
            user_id="u1",
            request="find the file",
            goal="find it",
            react_steps=2,
            tool_call_count=3,
            llm_call_count=4,
            errors=["earlier"],
        )
        task = SpecialistTask(agent_name="Reacter", goal="find it")
        channels = state_to_channels(state, agent_name="Reacter", task_json=task.model_dump())
        assert channels["run_id"] == state.run_id
        assert channels["goal"] == "find it"
        assert channels.get("messages", []) == []

        merged = apply_channels_to_state(
            state,
            {
                **channels,
                "usage": {
                    "react_steps": 2,
                    "tool_calls": 1,
                    "llm_calls": 3,
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "estimated_cost_usd": "0.0025",
                    "elapsed_seconds": 1.25,
                    "supervisor_iterations": 1,
                    "delegation_depth": 2,
                },
                "errors": ["late"],
            },
        )
        assert merged.react_steps == 4
        assert merged.tool_call_count == 4
        assert merged.llm_call_count == 7
        assert merged.prompt_tokens == 100
        assert merged.completion_tokens == 50
        assert merged.total_tokens == 150
        assert merged.estimated_cost == pytest.approx(0.0025)
        assert merged.elapsed_seconds == pytest.approx(1.25)
        assert merged.supervisor_iterations == 1
        assert merged.max_delegation_depth_reached == 2
        assert merged.errors == ["earlier", "late"]
        assert merged.request == "find the file"
        assert merged.goal == "find it"

    def test_state_to_channels_deepcopies_and_respects_approval_token(self) -> None:
        state = AssistantState(user_id="u1", request="do work")
        task_data = {
            "agent_name": "Writer",
            "goal": "write file",
            "permit_mutations": True,
            "approval_token": "appr_valid_token_123",
            "context_data": {"nested": {"key": "val"}},
        }
        channels = state_to_channels(state, agent_name="Writer", task_json=task_data)
        # permit_mutations stays True because approval_token is provided top-level
        assert channels["task_json"]["permit_mutations"] is True
        assert channels["task_json"]["approval_token"] == "appr_valid_token_123"

        # Mutating channels task_json does not mutate task_data (deep copy)
        channels["task_json"]["context_data"]["nested"]["key"] = "mutated"
        assert task_data["context_data"]["nested"]["key"] == "val"

        # If permit_mutations=True without any approval token, it is stripped to False
        task_no_token = {
            "agent_name": "Writer",
            "goal": "write file",
            "permit_mutations": True,
            "context_data": {},
        }
        channels_no_tok = state_to_channels(state, agent_name="Writer", task_json=task_no_token)
        assert channels_no_tok["task_json"]["permit_mutations"] is False



class TestCheckpointerDsn:
    def test_strips_asyncpg_marker(self) -> None:
        assert (
            derive_checkpointer_dsn("postgresql+asyncpg://u:p@localhost:5434/db")
            == "postgresql://u:p@localhost:5434/db"
        )

    def test_plain_postgres_passes_through(self) -> None:
        url = "postgresql://u:p@localhost:5432/db"
        assert derive_checkpointer_dsn(url) == url

    def test_non_postgres_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="must be PostgreSQL"):
            derive_checkpointer_dsn("sqlite+aiosqlite:///:memory:")

    def test_empty_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="empty"):
            derive_checkpointer_dsn("  ")


class TestLangGraphBoundary:
    def test_no_langgraph_import_outside_harness(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        app_dir = repo_root / "app"
        offenders: list[str] = []
        for path in sorted(app_dir.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                if any(mod == "langgraph" or mod.startswith("langgraph.") for mod in modules):
                    try:
                        path.relative_to(app_dir / "harness")
                    except ValueError:
                        offenders.append(str(path.relative_to(repo_root)))
        assert offenders == []

    async def test_graph_runs_direct_path(self) -> None:
        chat = ScriptedChat([fakes.text_turn("direct answer")])
        builder, run_id, user_id = _stack(chat, DictExecutor({}))
        compiled = builder.build()
        state = AssistantState(user_id=user_id, request="quick q")
        task = SpecialistTask(agent_name="Direct", goal="quick q", budget=ExecutionBudget())
        result = await compiled.ainvoke(
            state_to_channels(state, agent_name="Direct", task_json=task.model_dump())
        )
        assert result["mode"] == "direct"
        assert result["report_json"]["status"] == "success"
        assert result["report_json"]["summary"] == "direct answer"

    async def test_graph_runs_react_path_with_tools(self) -> None:
        chat = ScriptedChat(
            [
                fakes.calls_turn(("test.search", {"q": "x"})),
                fakes.report_turn(summary="react done"),
            ]
        )
        executor = DictExecutor({"test.search": lambda args: fakes.ok_result("test.search")})
        builder, run_id, user_id = _stack(chat, executor)
        compiled = builder.build()
        state = AssistantState(user_id=user_id, request="deep q")
        task = SpecialistTask(agent_name="Reacter", goal="deep q", budget=ExecutionBudget())
        result = await compiled.ainvoke(
            state_to_channels(state, agent_name="Reacter", task_json=task.model_dump())
        )
        assert result["mode"] == "bounded_react"
        assert result["report_json"]["summary"] == "react done"
        assert len(result["trace_steps"]) == 1
        assert result["usage"]["tool_calls"] == 1
