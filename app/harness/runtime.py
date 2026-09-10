"""Production composition root for harness dispatch (P16 H4).

Wires first-party agent/tool registries, a fail-closed chat backend (blocked
report when no LLM is configured), and a DelegationService-backed supervisor
executor. Live Google/retrieval providers remain P18.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.declarations import KNOWLEDGE_RESEARCH_AGENT_NAME, build_first_party_registry
from app.agents.specialist.delegation import DelegationService
from app.agents.specialist.react import SpecialistRunner
from app.agents.specialist.report import REPORT_TOOL_NAME
from app.core.config import settings
from app.domain.models.budget import ExecutionBudget
from app.domain.models.specialist import AssistantTurn, ToolCallRequest
from app.domain.models.tool import ToolContext, ToolExecutionMetadata, ToolInput, ToolResult
from app.harness.graph import SpecialistGraphBuilder
from app.harness.supervisor import SupervisorGraphBuilder, build_delegation_task_executor
from app.services.budget_manager import BudgetManager
from app.services.capability_gate import CapabilityGate
from app.services.context.compaction import ContextCompactor
from app.services.context.consolidation import BackgroundConsolidationWorker
from app.services.context.context_builder import ContextBuilder
from app.services.context.entity_resolver import EntityResolver
from app.services.context.entity_store import InMemoryEntityStore
from app.services.context.episodic_service import EpisodicMemoryService
from app.services.context.memory_gate import MemoryGate
from app.services.context.memory_store import InMemoryMemoryStore
from app.services.context.preference_service import PreferenceService
from app.services.supervisor import SupervisorPlanner, build_capability_catalog
from app.tools.ask_user import ask_user_tool_definitions
from app.tools.google_calendar import calendar_tool_definitions
from app.tools.google_communication import communication_tool_definitions
from app.tools.google_drive import drive_tool_definitions
from app.tools.knowledge import knowledge_tool_definitions
from app.tools.registry import ToolRegistry


class UnconfiguredChatBackend:
    """Fail-closed LLM seam: report blocked instead of inventing specialist output."""

    async def complete(self, messages: list[Any], tools: list[Any]) -> AssistantTurn:
        del messages, tools
        return AssistantTurn(
            text="",
            tool_calls=[
                ToolCallRequest(
                    tool_name=REPORT_TOOL_NAME,
                    arguments={
                        "status": "blocked",
                        "summary": (
                            "LLM chat backend is not configured; this specialist cannot execute."
                        ),
                        "blockers": ["chat_backend_unconfigured"],
                    },
                )
            ],
        )


class UnwiredToolExecutor:
    """Tool seam used until Google/retrieval services are injected at P18."""

    async def execute(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        if tool_input.tool_name in ("tool_ask_user", "ask_user"):
            from app.tools.ask_user import execute_ask_user

            return await execute_ask_user(tool_input, context)

        del context
        return ToolResult(
            tool_name=tool_input.tool_name,
            success=False,
            error="No live tool provider is wired in this process.",
            metadata=ToolExecutionMetadata(tool_name=tool_input.tool_name, latency_ms=0.0),
        )


class SpecialistChildRunner:
    """Adapter from DelegationService.ChildRunner to SpecialistRunner.run."""

    def __init__(self, runner: SpecialistRunner) -> None:
        self._runner = runner

    async def run_child(self, task: Any, agent: Any, tools: Any) -> Any:
        ctx = task.context_data if isinstance(task.context_data, dict) else {}
        run_id = str(ctx.get("run_id") or "delegated").strip() or "delegated"
        user_id = str(ctx.get("user_id") or "unknown").strip() or "unknown"
        return await self._runner.run(task, agent, tools, run_id=run_id, user_id=user_id)


def build_first_party_tool_registry() -> ToolRegistry:
    """Merge Calendar / Gmail / Drive / knowledge / interactive tool declarations."""
    return ToolRegistry(
        [
            *calendar_tool_definitions(),
            *communication_tool_definitions(),
            *drive_tool_definitions(),
            *knowledge_tool_definitions(),
            *ask_user_tool_definitions(),
        ]
    )


@dataclass
class DefaultContextRuntime:
    entity_store: InMemoryEntityStore
    memory_store: InMemoryMemoryStore
    entity_resolver: EntityResolver
    preference_service: PreferenceService
    episodic_service: EpisodicMemoryService
    memory_gate: MemoryGate
    context_builder: ContextBuilder
    compactor: ContextCompactor
    consolidation_worker: BackgroundConsolidationWorker


_default_context_runtime: DefaultContextRuntime | None = None


def get_default_context_runtime() -> DefaultContextRuntime:
    """Singleton default in-memory context and memory runtime (P17 M1)."""
    global _default_context_runtime
    if _default_context_runtime is None:
        entity_store = InMemoryEntityStore()
        memory_store = InMemoryMemoryStore()
        entity_resolver = EntityResolver(entity_store)
        preference_service = PreferenceService(memory_store)
        episodic_service = EpisodicMemoryService(memory_store)
        memory_gate = MemoryGate()
        context_builder = ContextBuilder(
            gate=memory_gate,
            entity_resolver=entity_resolver,
            preference_service=preference_service,
            episodic_service=episodic_service,
        )
        compactor = ContextCompactor()
        consolidation_worker = BackgroundConsolidationWorker(episodic_service, entity_resolver)
        _default_context_runtime = DefaultContextRuntime(
            entity_store=entity_store,
            memory_store=memory_store,
            entity_resolver=entity_resolver,
            preference_service=preference_service,
            episodic_service=episodic_service,
            memory_gate=memory_gate,
            context_builder=context_builder,
            compactor=compactor,
            consolidation_worker=consolidation_worker,
        )
    return _default_context_runtime


def build_default_specialist_graph_builder(
    compactor: ContextCompactor | None = None,
) -> SpecialistGraphBuilder:
    agents = build_first_party_registry()
    tools = build_first_party_tool_registry()
    gate = CapabilityGate(tools, agents)
    ctx_runtime = get_default_context_runtime()
    runner = SpecialistRunner(
        UnconfiguredChatBackend(),
        UnwiredToolExecutor(),
        compactor=compactor or ctx_runtime.compactor,
    )
    return SpecialistGraphBuilder(runner, agents, gate)


def build_default_supervisor_graph_builder(
    compactor: ContextCompactor | None = None,
) -> SupervisorGraphBuilder:
    agents = build_first_party_registry()
    tools = build_first_party_tool_registry()
    gate = CapabilityGate(tools, agents)
    ctx_runtime = get_default_context_runtime()
    runner = SpecialistRunner(
        UnconfiguredChatBackend(),
        UnwiredToolExecutor(),
        compactor=compactor or ctx_runtime.compactor,
    )
    delegation = DelegationService(agents, tools, gate, SpecialistChildRunner(runner))
    catalog = build_capability_catalog(agents)
    budget = ExecutionBudget(
        timeout_seconds=settings.supervisor_budget.timeout_seconds,
        max_supervisor_iterations=settings.supervisor_budget.max_iterations,
    )
    return SupervisorGraphBuilder(
        planner=SupervisorPlanner(),
        catalog=catalog,
        budget=budget,
        task_executor=build_delegation_task_executor(
            delegation, parent_agent=KNOWLEDGE_RESEARCH_AGENT_NAME
        ),
        budget_manager=BudgetManager(budget=budget),
        max_replans=settings.supervisor_budget.max_replans,
        timeout_seconds=settings.supervisor_budget.timeout_seconds,
    )


def build_default_meeting_prep_graph_builder(
    *,
    checkpointer: Any | None = None,
) -> Any:
    """Build the production default WF-05 MeetingPrepGraph with read-only tool adapters."""
    from app.harness.workflows.meeting_prep import assert_read_only_tool, build_meeting_prep_graph

    def default_calendar_finder(ctx: dict[str, Any]) -> dict[str, Any] | None:
        assert_read_only_tool("calendar.search_events")
        assert_read_only_tool("calendar.list_events")
        if "meeting_context" in ctx and ctx["meeting_context"]:
            return ctx["meeting_context"]
        if "event_id" in ctx:
            return {
                "event_id": ctx["event_id"],
                "title": ctx.get("title", f"Meeting on {ctx.get('query', '')}"),
                "attendees": ctx.get("attendees", []),
                "summary": ctx.get("summary", ""),
                "start_time": ctx.get("start_time"),
            }
        return None

    def default_email_researcher(ctx: dict[str, Any]) -> list[dict[str, Any]]:
        assert_read_only_tool("gmail.search_messages")
        assert_read_only_tool("gmail.get_thread")
        # Return real query items if present, else empty list (never fabricate placeholder evidence per H2)
        return ctx.get("email_items", [])

    def default_doc_researcher(ctx: dict[str, Any]) -> list[dict[str, Any]]:
        assert_read_only_tool("retrieval.retrieve")
        assert_read_only_tool("drive.search_files")
        # Return real doc items if present, else empty list (never fabricate placeholder evidence per H2)
        return ctx.get("doc_items", [])

    return build_meeting_prep_graph(
        calendar_finder=default_calendar_finder,
        email_researcher=default_email_researcher,
        doc_researcher=default_doc_researcher,
        checkpointer=checkpointer,
        allowed_tools=[
            "calendar.search_events",
            "calendar.list_events",
            "gmail.search_messages",
            "gmail.get_thread",
            "retrieval.retrieve",
            "drive.search_files",
        ],
    )
