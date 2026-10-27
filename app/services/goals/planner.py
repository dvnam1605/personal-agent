"""Break a goal into dated steps: a model plan when available, a generic plan otherwise."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.core.llm import llm_json

MAX_STEPS = 8
PLANNER_SYSTEM = (
    "Bạn lập kế hoạch công việc. Chia mục tiêu thành 3–8 bước cụ thể, theo thứ tự thực hiện, "
    "mỗi bước có hạn (YYYY-MM-DD) không muộn hơn hạn chót của mục tiêu và không sớm hơn hôm "
    'nay. Trả về JSON duy nhất: {"steps": [{"title": "...", "due": "YYYY-MM-DD"}]}.'
)
FALLBACK_STEPS = (
    "Xác định phạm vi, đầu việc và người liên quan",
    "Chuẩn bị nội dung và tài liệu",
    "Rà soát với các bên liên quan",
    "Hoàn tất và kiểm tra lần cuối",
)


def fallback_plan(today: date, due: date | None) -> list[dict[str, Any]]:
    """Four generic steps spread evenly up to the due date (two weeks when there is none)."""
    end = due if due is not None and due > today else today + timedelta(days=14)
    span = (end - today).days
    return [
        {
            "title": title,
            "due": (
                today + timedelta(days=round(span * (index + 1) / len(FALLBACK_STEPS)))
            ).isoformat(),
            "done": False,
        }
        for index, title in enumerate(FALLBACK_STEPS)
    ]


async def plan_steps(title: str, today: date, due: date | None) -> list[dict[str, Any]]:
    """Dated steps for ``title``; the model's plan is clamped to [today, due] and validated."""
    deadline = f"hạn chót {due.isoformat()}" if due else "không có hạn chót (dùng 2 tuần)"
    reply = await llm_json(
        PLANNER_SYSTEM,
        f"Hôm nay: {today.isoformat()}\nMục tiêu: {title}\nHạn: {deadline}",
        timeout=40.0,
    )
    steps = _validated(reply, today, due)
    return steps or fallback_plan(today, due)


def _validated(reply: Any, today: date, due: date | None) -> list[dict[str, Any]]:
    if not isinstance(reply, dict) or not isinstance(reply.get("steps"), list):
        return []
    end = due if due is not None and due >= today else today + timedelta(days=14)
    steps: list[dict[str, Any]] = []
    for raw in reply["steps"][:MAX_STEPS]:
        if not isinstance(raw, dict) or not str(raw.get("title") or "").strip():
            continue
        try:
            step_due = date.fromisoformat(str(raw.get("due")))
        except ValueError:
            step_due = end
        step_due = min(max(step_due, today), end)
        steps.append(
            {"title": str(raw["title"]).strip()[:200], "due": step_due.isoformat(), "done": False}
        )
    return steps
