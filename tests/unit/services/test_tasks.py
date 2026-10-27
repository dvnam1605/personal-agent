"""Unit tests for the to-do list: command parsing, the Google Tasks adapter and tools."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from app.domain.errors import ValidationError
from app.domain.models import ToolContext, ToolInput
from app.integrations.google_tasks import TasksAdapter
from app.services.google.auth import GoogleApiClient
from app.services.tasks import TaskCommand, parse_task_command, split_due
from app.tools.google_tasks import GoogleTasksTools

TODAY = date(2026, 10, 27)  # a Tuesday


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Việc cần làm của tôi hôm nay?", TaskCommand("list")),
        ("tôi còn việc gì", TaskCommand("list")),
        (
            "Thêm việc: gọi anh Nam hạn thứ 6",
            TaskCommand("create", "gọi anh Nam", date(2026, 10, 30)),
        ),
        (
            "nhắc tôi nộp báo cáo trước 15/11",
            TaskCommand("create", "nộp báo cáo", date(2026, 11, 15)),
        ),
        ("tạo task review PR ngày mai", TaskCommand("create", "review PR", date(2026, 10, 28))),
        ("Nhắc tôi mai mua vé", TaskCommand("create", "mua vé", date(2026, 10, 28))),
        ("thêm việc: họp với mai", TaskCommand("create", "họp với mai")),
        ("thêm việc: họp với Mai", TaskCommand("create", "họp với Mai")),
        ("thêm việc gọi chị mai", TaskCommand("create", "gọi chị mai")),
        (
            "thêm việc họp với mai ngày mai",
            TaskCommand("create", "họp với mai", date(2026, 10, 28)),
        ),
        ("thêm việc nộp báo cáo mai", TaskCommand("create", "nộp báo cáo", date(2026, 10, 28))),
        ("tạo task gặp mai thứ 6", TaskCommand("create", "gặp mai", date(2026, 10, 30))),
        ("Xong việc gọi anh Nam", TaskCommand("complete", "gọi anh Nam")),
        ("đánh dấu xong: nộp báo cáo", TaskCommand("complete", "nộp báo cáo")),
        ("xong rồi", None),
        ("Lịch ngày mai?", None),
        ("tìm quyết định bổ nhiệm", None),
    ],
)
def test_parse_task_command(query: str, expected: TaskCommand | None) -> None:
    assert parse_task_command(query, TODAY) == expected


def test_due_dates() -> None:
    assert split_due("x hôm nay", TODAY) == ("x", TODAY)
    assert split_due("x ngày kia", TODAY) == ("x", date(2026, 10, 29))
    assert split_due("x thứ 3", TODAY) == ("x", date(2026, 11, 3))
    assert split_due("x chủ nhật", TODAY) == ("x", date(2026, 11, 1))
    assert split_due("x 1/2", TODAY) == ("x", date(2027, 2, 1))
    assert split_due("x 31/2", TODAY) == ("x", None)
    assert split_due("x 5/1/2027", TODAY) == ("x", date(2027, 1, 5))
    assert split_due("không hạn", TODAY) == ("không hạn", None)


class FakeTransport:
    """Records requests; answers from a queue."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _next(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("POST", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._next("PATCH", url, **kwargs)


def _adapter(*payloads: Any) -> tuple[TasksAdapter, FakeTransport]:
    transport = FakeTransport([httpx.Response(200, json=payload) for payload in payloads])
    client = GoogleApiClient(_access_token="t", base_url="https://google.test", transport=transport)
    return TasksAdapter(client), transport


async def test_adapter_lists_inserts_and_completes() -> None:
    adapter, transport = _adapter(
        {"items": [{"id": "t1", "title": "Gọi Nam", "due": "2026-10-30T00:00:00.000Z"}]},
        {"id": "t2", "title": "Nộp báo cáo", "status": "needsAction"},
        {
            "id": "t1",
            "title": "Gọi Nam",
            "status": "completed",
            "completed": "2026-10-27T03:00:00Z",
        },
    )
    items = await adapter.list_tasks()
    assert items[0].due == date(2026, 10, 30) and not items[0].is_done
    created = await adapter.insert_task("  Nộp   báo cáo ", due=date(2026, 11, 15))
    assert created.id == "t2"
    done = await adapter.complete_task("t1")
    assert done.is_done

    (m1, u1, k1), (m2, u2, k2), (m3, u3, k3) = transport.calls
    assert (m1, u1) == ("GET", "https://tasks.googleapis.com/tasks/v1/lists/@default/tasks")
    assert k1["params"]["showCompleted"] is False
    assert m2 == "POST" and k2["json"] == {
        "title": "Nộp báo cáo",
        "due": "2026-11-15T00:00:00.000Z",
    }
    assert (m3, u3) == ("PATCH", "https://tasks.googleapis.com/tasks/v1/lists/@default/tasks/t1")
    assert k3["json"] == {"status": "completed"}

    with pytest.raises(ValidationError):
        await adapter.insert_task("   ")
    with pytest.raises(ValidationError):
        await adapter.complete_task("a/b")


async def test_tools_refuse_writes_without_an_approval_token() -> None:
    adapter, transport = _adapter({"items": []})
    tools = GoogleTasksTools(adapter)
    context = ToolContext(run_id="r1", user_id="u1", agent_name="test")

    listed = await tools.execute(ToolInput(tool_name="tasks.list", arguments={}), context)
    assert listed.success and listed.output == []

    created = await tools.execute(
        ToolInput(tool_name="tasks.create", arguments={"title": "x", "due": None}), context
    )
    assert not created.success
    assert len(transport.calls) == 1

    unknown = await tools.execute(ToolInput(tool_name="tasks.delete", arguments={}), context)
    assert not unknown.success
