"""Agentic supervisor bridge: graph outcomes become QueryResults, writes become approvals."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.routing import agentic_bridge
from app.services.routing.agentic_bridge import AgenticRunError, run_agentic_supervisor
from app.services.routing.query import QueryRouteInfo

ROUTE = QueryRouteInfo(route_type="supervisor", domains=["knowledge", "calendar"], confidence=0.9)
PLAN = SimpleNamespace(
    tasks=[
        SimpleNamespace(id="t1", name="Tra quy chế", assigned_agent="KnowledgeResearchAgent"),
        SimpleNamespace(id="t2", name="Soạn email", assigned_agent="CommunicationAgent"),
    ]
)


class _Graph:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.inputs: dict[str, Any] = {}

    async def ainvoke(self, channels: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        self.inputs = channels
        return self.state


def _builder(state: dict[str, Any]) -> tuple[Any, _Graph]:
    graph = _Graph(state)
    return (lambda: SimpleNamespace(build=lambda: graph)), graph


async def _run(state: dict[str, Any], **kwargs: Any) -> Any:
    builder, _ = _builder(state)
    return await run_agentic_supervisor(
        session=SimpleNamespace(),  # type: ignore[arg-type]
        user_id="u1",
        query="Tra quy chế công tác phí rồi soạn email cho an@example.com",
        run_id="r1",
        route=ROUTE,
        builder=builder,
        **kwargs,
    )


async def test_completed_run_is_written_up_from_untrusted_outputs() -> None:
    seen: dict[str, str] = {}

    async def synthesize(query: str, evidence: str) -> str:
        seen["evidence"] = evidence
        return "Phụ cấp lưu trú là 200.000 đồng/ngày."

    result = await _run(
        {
            "status": "completed",
            "plan": PLAN,
            "task_results": {"t1": {"status": "completed", "output": "Mức 200.000 đồng/ngày"}},
        },
        synthesize=synthesize,
    )
    assert result.status == "completed"
    assert result.message == "Phụ cấp lưu trú là 200.000 đồng/ngày."
    assert "<untrusted_tool_result" in seen["evidence"]
    assert result.data["engine"] == "agentic"
    assert [t["status"] for t in result.data["plan"]] == ["completed", "pending"]


async def test_synthesis_failure_falls_back_to_the_raw_outputs() -> None:
    async def synthesize(query: str, evidence: str) -> None:
        return None

    result = await _run(
        {"status": "completed", "plan": PLAN, "task_results": {"t1": {"output": "Mức A"}}},
        synthesize=synthesize,
    )
    assert "Mức A" in result.message


@pytest.mark.parametrize("status", ["failed", "validation_failed", "no_progress_halted"])
async def test_unusable_runs_raise_so_the_caller_can_fall_back(status: str) -> None:
    with pytest.raises(AgenticRunError):
        await _run({"status": status, "plan": PLAN, "task_results": {}})


async def test_missing_context_asks_the_user() -> None:
    result = await _run(
        {"status": "needs_more_context", "missing_context": ["Gửi cho ai?", "Gửi cho ai?"]}
    )
    assert result.status == "clarification_needed"
    assert result.message.count("Gửi cho ai?") == 1


async def test_proposed_draft_becomes_an_approval_after_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []

    async def prepare(query: str, subject: str, body: str, recipients: list[str], **kw: Any) -> Any:
        assert recipients == ["an@example.com"]
        assert "200.000" in kw["context"]
        draft = SimpleNamespace(
            subject=subject, body=body + "\n\nTrân trọng", recipients=recipients
        )
        return SimpleNamespace(needs_recipients=False, draft=draft)

    async def create_request(session: Any, run_id: str, proposal: Any) -> Any:
        created.append(proposal)
        return SimpleNamespace(id="ap1")

    monkeypatch.setattr(agentic_bridge, "prepare_draft", prepare)
    monkeypatch.setattr(agentic_bridge.ApprovalRequestService, "create_request", create_request)
    result = await _run(
        {
            "status": "needs_approval",
            "plan": PLAN,
            "task_results": {
                "t1": {"status": "completed", "output": "Mức 200.000 đồng/ngày"},
                "t2": {"status": "needs_approval", "output": "Cần duyệt bản nháp"},
            },
            "needs_approval": [
                {
                    "task_id": "t2",
                    "proposed_action": {
                        "tool_name": "gmail.create_draft",
                        "arguments": {
                            "to": "an@example.com",
                            "subject": "Báo cáo",
                            "body": "Nội dung",
                        },
                    },
                }
            ],
        }
    )
    assert result.status == "needs_approval" and result.approval_id == "ap1"
    assert created[0].tool_name == "gmail.create_draft"
    assert created[0].parameters["body_text"].endswith("Trân trọng")
    assert "Cần duyệt bản nháp" not in result.message


async def test_a_proposal_other_than_an_email_draft_is_refused() -> None:
    with pytest.raises(AgenticRunError):
        await _run(
            {
                "status": "needs_approval",
                "needs_approval": [
                    {"proposed_action": {"tool_name": "gmail.send_message", "arguments": {}}}
                ],
            }
        )
