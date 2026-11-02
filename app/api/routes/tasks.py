"""To-do workspace endpoints: list Google Tasks, and propose adding or completing one.

Reading is direct. A change is never made here: proposing creates an approval request
bound to the exact title, due date or task id, and the web app approves it through
``POST /approvals/{id}/approve`` like every other Google write.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.api.routes.query import get_query_orchestrator
from app.domain.enums import ActionRiskLevel
from app.domain.errors import NotFoundError
from app.domain.models import ProposedAction
from app.infrastructure.db.session import get_db_session
from app.integrations.google_tasks import TaskItem
from app.services.approvals import ApprovalRequestService
from app.services.platform.run_persistence import RunPersistenceService
from app.services.routing.query import QueryOrchestrator
from app.tools.google_tasks import TASKS_COMPLETE, TASKS_CREATE

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskProposal(BaseModel):
    """What the user wants changed: add a task, or mark one done."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["create", "complete"]
    title: str = Field(default="", max_length=1024)
    due: date | None = None
    task_id: str | None = Field(default=None, max_length=256)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        return " ".join(value.split())

    @model_validator(mode="after")
    def check_fields(self) -> TaskProposal:
        if self.action == "create" and not self.title:
            raise ValueError("title is required to add a task")
        if self.action == "complete" and not self.task_id:
            raise ValueError("task_id is required to complete a task")
        return self


class ProposalResponse(BaseModel):
    """The approval the web app shows and approves."""

    approval_id: str
    description: str


@router.get("", response_model=list[TaskItem])
async def list_tasks(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    orchestrator: QueryOrchestrator = Depends(get_query_orchestrator),  # noqa: B008
) -> list[TaskItem]:
    """The caller's open tasks in their default Google Tasks list."""
    adapter = await orchestrator._tasks(session, user_id)
    return [item for item in await adapter.list_tasks() if not item.is_done]


@router.post("/propose", response_model=ProposalResponse)
async def propose_task_change(
    body: TaskProposal,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    orchestrator: QueryOrchestrator = Depends(get_query_orchestrator),  # noqa: B008
) -> ProposalResponse:
    """Create the approval request for adding or completing one of the caller's tasks."""
    parameters: dict[str, Any]
    if body.action == "create":
        when = f" (hạn {body.due:%d/%m/%Y})" if body.due else ""
        description = f"Thêm việc “{body.title}”{when} vào Google Tasks."
        tool, action_type = TASKS_CREATE, "create_task"
        parameters = {"title": body.title, "due": body.due.isoformat() if body.due else None}
    else:
        adapter = await orchestrator._tasks(session, user_id)
        task = next((t for t in await adapter.list_tasks() if t.id == body.task_id), None)
        if task is None or task.is_done:
            raise NotFoundError("Task not found.")
        description = f"Đánh dấu xong việc “{task.title}” trên Google Tasks."
        tool, action_type = TASKS_COMPLETE, "complete_task"
        parameters = {"task_id": task.id, "title": task.title}

    run_id = str(uuid.uuid4())
    await orchestrator._ensure_user(session, user_id)
    await RunPersistenceService.create_run(
        session,
        run_id=run_id,
        user_id=user_id,
        request=description,
        route_type="direct_specialist",
        domains=["general"],
        complexity="direct",
        correlation_id=run_id,
        active_skill="TasksWorkspace",
    )
    proposal = ProposedAction(
        action_type=action_type,
        description=description,
        tool_name=tool,
        parameters=parameters,
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        requires_approval=True,
    )
    approval = await ApprovalRequestService.create_request(session, run_id, proposal)
    return ProposalResponse(approval_id=approval.id, description=description)
