"""Unit tests for the evaluator–optimizer loop and the email draft critic."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.critic import (
    Critique,
    DraftCritic,
    EmailDraft,
    EvaluatorOptimizer,
    confirmed_recipients,
    fill_signature,
    structural_issues,
    valid_recipients,
)
from app.services.critic import draft as draft_module

BODY = (
    "Kính gửi anh Nam,\n\nEm xin phép nghỉ ngày mai vì việc gia đình.\n\nTrân trọng,\n[Tên của bạn]"
)


async def test_loop_revises_until_accepted_or_budget_spent() -> None:
    seen: list[int] = []

    async def critique(value: int) -> Critique:
        seen.append(value)
        return Critique(ok=value >= 2, issues=[] if value >= 2 else ["nhỏ quá"])

    async def revise(value: int, _verdict: Critique) -> int:
        return value + 1

    accepted = await EvaluatorOptimizer(critique, revise, max_revisions=5).run(0)
    assert accepted.output == 2 and accepted.accepted and accepted.revisions == 2

    capped = await EvaluatorOptimizer(critique, revise, max_revisions=1).run(-5)
    assert capped.output == -4 and not capped.accepted and capped.revisions == 1


async def test_loop_stops_when_reviser_gives_up() -> None:
    async def critique(_value: str) -> Critique:
        return Critique(ok=False, issues=["x"])

    async def give_up(_value: str, _verdict: Critique) -> None:
        return None

    result = await EvaluatorOptimizer(critique, give_up).run("draft")
    assert result.output == "draft" and len(result.critiques) == 1
    with pytest.raises(ValueError):
        EvaluatorOptimizer(critique, give_up, max_revisions=-1)


def test_recipient_and_structure_checks() -> None:
    assert valid_recipients(["Nam@X.vn", "anh Nam", "nam@x.vn", "lan@y.com"]) == (
        "nam@x.vn",
        "lan@y.com",
    )
    draft = EmailDraft(subject="Xin nghỉ", body=BODY, recipients=("nam@x.vn", "quanly@vov.vn"))
    assert confirmed_recipients(draft, "Soạn email gửi nam@x.vn", "") == ("nam@x.vn",)
    assert confirmed_recipients(draft, "anh Nam") == ()

    assert "Còn chỗ trống chưa điền (ví dụ [Tên của bạn])." in structural_issues(draft)
    filled = fill_signature(draft, "Đậu Văn Nam")
    assert filled.body.endswith("Đậu Văn Nam") and structural_issues(filled) == []
    assert fill_signature(draft, None) is draft
    assert structural_issues(EmailDraft(subject="", body="ngắn", recipients=())) == [
        "Thiếu tiêu đề.",
        "Nội dung quá ngắn.",
    ]


async def test_draft_critic_uses_model_review_and_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_llm_json(system: str, user: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(system[:20])
        if system is draft_module.CRITIC_SYSTEM:
            ok = "Trân trọng, Nam" in user
            return {"ok": ok, "issues": [] if ok else ["Thiếu ký tên."]}
        return {
            "subject": "Xin nghỉ phép",
            "body": BODY.replace("[Tên của bạn]", "Trân trọng, Nam"),
        }

    monkeypatch.setattr(draft_module, "llm_json", fake_llm_json)
    draft = EmailDraft(subject="Xin nghỉ", body=BODY, recipients=("nam@x.vn",))
    reviewed = await DraftCritic(max_revisions=2).review("xin nghỉ ngày mai", draft)

    assert reviewed.accepted
    assert reviewed.output.subject == "Xin nghỉ phép"
    assert reviewed.critiques[0].issues[0].startswith("Còn chỗ trống")
    assert len(calls) == 3


async def test_draft_critic_without_model_reports_structure_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_llm(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(draft_module, "llm_json", no_llm)
    clean = EmailDraft(subject="Xin nghỉ", body=BODY.replace("[Tên của bạn]", "Nam"), recipients=())
    reviewed = await DraftCritic().review("xin nghỉ", clean)
    assert reviewed.accepted and reviewed.revisions == 0
