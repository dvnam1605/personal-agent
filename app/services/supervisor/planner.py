"""Supervisor structured planner and bounded replan engine (spec P16-01 / P16-06 / P16-07).

Emits typed ExecutionPlan DAGs using high-level CapabilityCatalog views.
No low-level tools are seen or chosen by this planner.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Protocol, runtime_checkable

from app.domain.errors import ValidationError
from app.domain.models.platform.budget import ExecutionBudget
from app.domain.models.supervisor import CapabilityCatalog
from app.domain.models.supervisor.plan import ExecutionPlan, ExecutionTask, TaskDependency
from app.domain.models.supervisor.specialist import AssistantTurn, ChatMessage

logger = logging.getLogger(__name__)


@runtime_checkable
class PlannerChatBackend(Protocol):
    """Protocol for LLM interactions in the supervisor planner."""

    async def complete(
        self,
        messages: list[ChatMessage],
    ) -> AssistantTurn: ...


SUPERVISOR_SYSTEM_PROMPT = """You are the Supervisor Orchestrator.
Your job is to break down complex multi-domain requests into a directed acyclic graph (DAG) of tasks.
You only assign tasks to designated specialist agents based on their capabilities.
You DO NOT execute tasks yourself.

Available Specialists & Capabilities:
{catalog_prompt}

Rules:
1. Break down the user request into clear, discrete tasks.
2. For each task, specify:
   - "id": a unique string ID (e.g. "task_1", "task_2")
   - "name": a short title
   - "assigned_agent": the exact name of the specialist agent
   - "description": what the specialist must do and find
   - "dependencies": array of task IDs that MUST complete before this task can start.
     - If tasks can run concurrently (e.g. searching calendar and searching documents independently), DO NOT make them depend on each other.
   - "input_data": dict of input parameters (e.g. query, attendee emails, keywords)
3. Return ONLY a valid JSON object with the following schema:
{{
  "goal": "summary of the overall goal",
  "tasks": [
    {{
      "id": "task_1",
      "name": "...",
      "assigned_agent": "...",
      "description": "...",
      "dependencies": [],
      "input_data": {{}}
    }}
  ]
}}
"""


class SupervisorPlanner:
    """Emits structured ExecutionPlan objects using capability-only views."""

    def __init__(
        self,
        chat_backend: PlannerChatBackend | None = None,
    ) -> None:
        self._chat = chat_backend

    async def plan(
        self,
        query: str,
        goal: str,
        catalog: CapabilityCatalog,
        budget: ExecutionBudget,
        context: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        """Create an initial ExecutionPlan from query, goal, and capability catalog."""
        if not self._chat:
            return self._heuristic_plan(query, goal, catalog)

        system_msg = ChatMessage(
            role="system",
            content=SUPERVISOR_SYSTEM_PROMPT.format(catalog_prompt=catalog.format_prompt_catalog()),
        )
        user_content = f"User Request: {query}\nGoal: {goal}"
        if context:
            user_content += f"\nContext: {json.dumps(context, ensure_ascii=False)}"
        user_msg = ChatMessage(role="user", content=user_content)

        turn = await self._chat.complete([system_msg, user_msg])
        return self._parse_plan_json(turn.text, goal)

    async def replan(
        self,
        original_plan: ExecutionPlan,
        completed_tasks: list[ExecutionTask],
        missing_context: list[str],
        catalog: CapabilityCatalog,
        budget: ExecutionBudget,
    ) -> ExecutionPlan:
        """Produce an updated ExecutionPlan to resolve missing context (P16-06)."""
        if not self._chat:
            return self._heuristic_replan(original_plan, completed_tasks, missing_context, catalog)

        system_msg = ChatMessage(
            role="system",
            content=SUPERVISOR_SYSTEM_PROMPT.format(catalog_prompt=catalog.format_prompt_catalog()),
        )
        completed_summary = [
            {
                "id": t.id,
                "name": t.name,
                "assigned_agent": t.assigned_agent,
                "status": t.status.value,
            }
            for t in completed_tasks
        ]
        replan_prompt = (
            f"Original Goal: {original_plan.goal}\n"
            f"Completed Tasks: {json.dumps(completed_summary, ensure_ascii=False)}\n"
            f"Missing Context / Needs: {json.dumps(missing_context, ensure_ascii=False)}\n\n"
            "Create an updated execution plan with NEW tasks to acquire the missing context and complete the goal. "
            "Do not re-execute successfully completed tasks."
        )
        user_msg = ChatMessage(role="user", content=replan_prompt)

        turn = await self._chat.complete([system_msg, user_msg])
        return self._parse_plan_json(turn.text, original_plan.goal)

    def detect_no_progress(
        self,
        previous_plans: list[ExecutionPlan],
        new_plan: ExecutionPlan,
    ) -> bool:
        """Detect if the new plan repeats tasks from previous plans without progress (P16-07).

        M6: include description hash in signature to catch description-only changes.
        """
        if not previous_plans:
            return False

        import hashlib as _hashlib

        def _sig(t: ExecutionTask) -> tuple[str, str, str, tuple[str, ...]]:
            desc_hash = _hashlib.md5(t.description.encode()).hexdigest()[:8]
            return (
                t.assigned_agent,
                t.name.strip().lower(),
                desc_hash,
                tuple(sorted(t.dependency_ids)),
            )

        new_signatures = {_sig(t) for t in new_plan.tasks}

        for prev in previous_plans:
            prev_signatures = {_sig(t) for t in prev.tasks}
            if new_signatures == prev_signatures:
                logger.warning(
                    "no_progress_detected_identical_plan", extra={"plan_id": new_plan.plan_id}
                )
                return True

        return False

    # ------------------------------------------------------------------
    # Parsing and Heuristic Fallbacks
    # ------------------------------------------------------------------

    def _parse_plan_json(self, raw_text: str, default_goal: str) -> ExecutionPlan:
        cleaned = raw_text.strip()
        # Strip markdown fences if present
        if cleaned.startswith("```json"):
            cleaned = cleaned[len("```json") :].strip()
        if cleaned.startswith("```"):
            cleaned = cleaned[3:].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"Supervisor failed to output valid JSON: {exc}", details={"raw": raw_text}
            ) from exc

        goal = data.get("goal") or default_goal
        raw_tasks = data.get("tasks", [])
        tasks: list[ExecutionTask] = []

        for item in raw_tasks:
            deps: list[TaskDependency] = []
            task_id = item.get("id") or str(uuid.uuid4())
            for dep_id in item.get("dependencies", []):
                deps.append(TaskDependency(task_id=task_id, depends_on_task_id=dep_id))

            task = ExecutionTask(
                id=task_id,
                name=item.get("name", f"Task {task_id}"),
                assigned_agent=item.get("assigned_agent", ""),
                description=item.get("description", ""),
                dependencies=deps,
                input_data=item.get("input_data", {}),
            )
            tasks.append(task)

        return ExecutionPlan(goal=goal, tasks=tasks)

    def _heuristic_plan(
        self,
        query: str,
        goal: str,
        catalog: CapabilityCatalog,
    ) -> ExecutionPlan:
        """Deterministic heuristic fallback when no LLM chat backend is provided.

        M6: use broader capability patterns (gmail.*, calendar.*, retrieval.*)
        to match actual registry names instead of exact misses like schedule_meeting.
        """
        tasks: list[ExecutionTask] = []
        lower_q = query.lower()

        # Check domain presence
        needs_calendar = any(
            w in lower_q for w in ["họp", "lịch", "calendar", "meeting", "ngày mai", "tuần này"]
        )
        needs_comm = any(
            w in lower_q for w in ["email", "thư", "nhắn", "gửi", "soạn", "mail", "team"]
        )
        needs_doc = any(
            w in lower_q
            for w in ["tài liệu", "quy định", "chính sách", "tìm", "tra cứu", "báo cáo"]
        )

        # Create tasks — M6: broader capability lookup patterns
        doc_task_id = "task_search_docs"
        cal_task_id = "task_check_cal"
        comm_task_id = "task_draft_comm"

        if needs_doc:
            agent = catalog.get_agent_for_capability(
                "search_internal_documents"
            ) or catalog.get_agent_for_capability("retrieval.*")
            if agent is None:
                logger.warning("planner_capability_unresolved", extra={"capability": "retrieval.*"})
            else:
                tasks.append(
                    ExecutionTask(
                        id=doc_task_id,
                        name="Tra cứu tài liệu quy định",
                        assigned_agent=agent,
                        description=f"Tìm kiếm thông tin liên quan đến: {query}",
                        input_data={"query": query},
                        dependencies=[],
                    )
                )

        if needs_calendar:
            agent = catalog.get_agent_for_capability(
                "schedule_meeting"
            ) or catalog.get_agent_for_capability("calendar.*")
            if agent is None:
                logger.warning("planner_capability_unresolved", extra={"capability": "calendar.*"})
            else:
                tasks.append(
                    ExecutionTask(
                        id=cal_task_id,
                        name="Kiểm tra lịch và phòng họp",
                        assigned_agent=agent,
                        description=f"Kiểm tra lịch trình cho yêu cầu: {query}",
                        input_data={"query": query},
                        dependencies=[],
                    )
                )

        if needs_comm:
            agent = catalog.get_agent_for_capability(
                "draft_email"
            ) or catalog.get_agent_for_capability("gmail.*")
            if agent is None:
                logger.warning("planner_capability_unresolved", extra={"capability": "gmail.*"})
            else:
                # Comm task depends on earlier research/calendar tasks if they exist
                deps = [
                    TaskDependency(task_id=comm_task_id, depends_on_task_id=t.id) for t in tasks
                ]
                tasks.append(
                    ExecutionTask(
                        id=comm_task_id,
                        name="Soạn thảo thông báo và email",
                        assigned_agent=agent,
                        description=f"Soạn thông báo tổng hợp dựa trên kết quả: {query}",
                        input_data={"query": query},
                        dependencies=deps,
                    )
                )

        if not tasks:
            # Default fallback task
            default_agent = (
                catalog.agents[0].agent_name if catalog.agents else "KnowledgeResearchAgent"
            )
            tasks.append(
                ExecutionTask(
                    id="task_default",
                    name="Xử lý yêu cầu người dùng",
                    assigned_agent=default_agent,
                    description=query,
                    input_data={"query": query},
                    dependencies=[],
                )
            )

        return ExecutionPlan(goal=goal, tasks=tasks)

    def _heuristic_replan(
        self,
        original_plan: ExecutionPlan,
        completed_tasks: list[ExecutionTask],
        missing_context: list[str],
        catalog: CapabilityCatalog,
    ) -> ExecutionPlan:
        """Deterministic heuristic replan targeting missing context."""
        completed_ids = {t.id for t in completed_tasks}
        new_tasks: list[ExecutionTask] = [t for t in original_plan.tasks if t.id in completed_ids]

        # Add follow-up task to resolve missing context
        # M6: use uuid-based task ID to avoid collisions across replans
        followup_id = f"replan_{uuid.uuid4().hex[:8]}"
        agent = catalog.get_agent_for_capability(
            "search_internal_documents"
        ) or catalog.get_agent_for_capability("retrieval.*")
        if agent is None:
            agent = catalog.agents[0].agent_name if catalog.agents else "KnowledgeResearchAgent"
            logger.warning("planner_replan_capability_unresolved", extra={"fallback": agent})

        new_tasks.append(
            ExecutionTask(
                id=followup_id,
                name="Bổ sung thông tin còn thiếu",
                assigned_agent=agent,
                description=f"Tra cứu bổ sung cho: {'; '.join(missing_context)}",
                input_data={"query": "; ".join(missing_context)},
                dependencies=[],
            )
        )

        # Re-link pending downstream tasks
        for t in original_plan.tasks:
            if t.id not in completed_ids:
                updated_deps = list(t.dependencies) + [
                    TaskDependency(task_id=t.id, depends_on_task_id=followup_id)
                ]
                new_tasks.append(
                    ExecutionTask(
                        id=t.id,
                        name=t.name,
                        assigned_agent=t.assigned_agent,
                        description=t.description,
                        input_data=t.input_data,
                        dependencies=updated_deps,
                    )
                )

        return ExecutionPlan(goal=original_plan.goal, tasks=new_tasks)
