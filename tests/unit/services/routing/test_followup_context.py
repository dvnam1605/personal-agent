"""Follow-ups are recognised from the previous answer, then made standalone."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.enums.enums import RouteType
from app.services.documents import llm as llm_module
from app.services.routing.query.orchestrator import (
    QueryOrchestrator,
    answer_anchor,
    standalone_followup,
)

ANSWER = """3. Xu hướng phát triển tương lai của Hệ thống Đa tác tử
* 3 giai đoạn tiến hóa của hệ thống điều phối tác tử:
1. Giai đoạn 1 (Hiện tại): Thống trị bởi các mẫu quy trình công việc xác định (Workflow patterns) [1].
2. Giai đoạn 2: Áp dụng rộng rãi các mẫu tự trị (Autonomous patterns) [2].
3. Giai đoạn 3: Tích hợp (Integration) - các trợ lý đa năng tự động lựa chọn giữa workflow và autonomous [3].
* Thị trường tác tử (Agent Marketplaces): các chợ tác tử có thể tái sử dụng [4]."""

HISTORY = [
    {
        "role": "user",
        "content": "Xu hướng phát triển tương lai của hệ thống đa tác tử trong sách là gì?",
    },
    {"role": "assistant", "content": ANSWER},
]


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def offline(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(llm_module, "llm_text", offline)


def test_anchor_is_the_line_the_turn_points_at() -> None:
    anchor = answer_anchor("trình bày chi tiết cho tôi giai đoạn 3 đi", HISTORY)
    assert anchor and anchor.startswith("3. Giai đoạn 3: Tích hợp (Integration)")
    assert "[3]" not in anchor


def test_anchor_works_for_any_term_of_the_answer() -> None:
    anchor = answer_anchor("agent marketplaces hoạt động ra sao", HISTORY)
    assert anchor and "Thị trường tác tử" in anchor


def test_new_unrelated_question_has_no_anchor() -> None:
    assert answer_anchor("Lịch họp ngày mai của tôi thế nào?", HISTORY) is None
    assert answer_anchor("trình bày chi tiết hơn cho tôi", HISTORY) is None  # request words only


async def test_standalone_question_carries_the_subject() -> None:
    anchor = answer_anchor("trình bày chi tiết cho tôi giai đoạn 3 đi", HISTORY) or ""
    q = await standalone_followup("trình bày chi tiết cho tôi giai đoạn 3 đi", HISTORY, anchor)
    assert "Integration" in q and "hệ thống đa tác tử" in q


async def test_llm_rewrite_is_used_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    async def rewrite(*_: Any, **__: Any) -> str:
        return "Trình bày chi tiết giai đoạn 3 (Integration) trong điều phối hệ đa tác tử"

    monkeypatch.setattr(llm_module, "llm_text", rewrite)
    q = await standalone_followup("chi tiết giai đoạn 3", HISTORY, "Giai đoạn 3: Tích hợp")
    assert q == "Trình bày chi tiết giai đoạn 3 (Integration) trong điều phối hệ đa tác tử"


async def test_vague_followup_is_routed_to_the_knowledge_base() -> None:
    effective, decision, anchor = await QueryOrchestrator()._triage_in_context(
        "trình bày chi tiết cho tôi giai đoạn 3 đi", HISTORY
    )
    assert decision.route_type is not RouteType.CLARIFICATION
    assert "Integration" in effective


async def test_without_history_nothing_is_rewritten() -> None:
    effective, _, anchor = await QueryOrchestrator()._triage_in_context("làm cái đó đi", [])
    assert effective == "làm cái đó đi"
