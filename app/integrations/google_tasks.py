"""Google Tasks adapter: list open tasks, add a task, mark a task done."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import ValidationError as DomainValidationError
from app.integrations.google_common import GoogleResourceAdapter, require_list, require_object

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
DEFAULT_TASKLIST = "@default"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"
MAX_TITLE_LEN = 1024
MAX_NOTES_LEN = 8192


class TaskItem(BaseModel):
    """One Google task, normalized: due is a calendar date (Tasks ignores the time)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    title: str = ""
    notes: str | None = None
    status: str = "needsAction"
    due: date | None = None
    completed_at: datetime | None = None
    web_link: str | None = None

    @property
    def is_done(self) -> bool:
        return self.status == "completed"


def _identifier(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 256 or any(c in normalized for c in "\r\n/"):
        raise DomainValidationError(f"Invalid Google Tasks {label}.")
    return normalized


def _task_from_payload(raw: Any) -> TaskItem:
    data = require_object(raw, "Google Tasks item")
    due_raw = data.get("due")
    completed_raw = data.get("completed")
    return TaskItem(
        id=str(data.get("id") or ""),
        title=str(data.get("title") or ""),
        notes=str(data["notes"]) if data.get("notes") else None,
        status=str(data.get("status") or "needsAction"),
        due=datetime.fromisoformat(str(due_raw).replace("Z", "+00:00")).date() if due_raw else None,
        completed_at=(
            datetime.fromisoformat(str(completed_raw).replace("Z", "+00:00"))
            if completed_raw
            else None
        ),
        web_link=str(data["webViewLink"]) if data.get("webViewLink") else None,
    )


class TasksAdapter(GoogleResourceAdapter):
    """Typed Google Tasks calls on one task list (the user's default list by default)."""

    required_scope = TASKS_SCOPE

    async def list_tasks(
        self,
        tasklist: str = DEFAULT_TASKLIST,
        *,
        show_completed: bool = False,
        max_results: int = 100,
    ) -> list[TaskItem]:
        """Open tasks (or all, with ``show_completed``), in the list's own order."""
        identifier = _identifier(tasklist, "task list")
        params: dict[str, Any] = {
            "maxResults": max(1, min(max_results, 100)),
            "showCompleted": show_completed,
            "showHidden": show_completed,
        }
        payload = await self._request_json(
            "GET",
            f"{TASKS_API}/lists/{quote(identifier, safe='@')}/tasks",
            operation="Google Tasks listing",
            params=params,
        )
        return [
            _task_from_payload(item)
            for item in require_list(payload, "items", "Google Tasks listing")
        ]

    async def insert_task(
        self,
        title: str,
        *,
        notes: str | None = None,
        due: date | None = None,
        tasklist: str = DEFAULT_TASKLIST,
    ) -> TaskItem:
        """Add a task at the top of the list."""
        clean_title = " ".join(title.split())
        if not clean_title or len(clean_title) > MAX_TITLE_LEN:
            raise DomainValidationError("Task title must be 1–1024 characters.")
        body: dict[str, Any] = {"title": clean_title}
        if notes:
            body["notes"] = notes[:MAX_NOTES_LEN]
        if due is not None:
            body["due"] = f"{due.isoformat()}T00:00:00.000Z"
        payload = await self._request_json(
            "POST",
            f"{TASKS_API}/lists/{quote(_identifier(tasklist, 'task list'), safe='@')}/tasks",
            operation="Google Tasks insert",
            json=body,
        )
        return _task_from_payload(payload)

    async def complete_task(self, task_id: str, *, tasklist: str = DEFAULT_TASKLIST) -> TaskItem:
        """Mark a task completed."""
        list_id = quote(_identifier(tasklist, "task list"), safe="@")
        task = quote(_identifier(task_id, "task id"), safe="")
        payload = await self._request_json(
            "PATCH",
            f"{TASKS_API}/lists/{list_id}/tasks/{task}",
            operation="Google Tasks completion",
            json={"status": "completed"},
        )
        return _task_from_payload(payload)


__all__ = ["DEFAULT_TASKLIST", "TASKS_SCOPE", "TaskItem", "TasksAdapter"]
