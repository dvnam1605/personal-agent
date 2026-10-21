"""Section self-review: check against cited evidence, rewrite once, trim what is still wrong."""

from __future__ import annotations

from typing import Any

from app.services.documents.review import SectionReviewer, remove_flagged_sentences
from app.services.documents.service import review_note

EVIDENCE = {
    1: "Quyết định số 91/QĐ-TNVN ngày 16/01/2026 tặng Bằng khen cho Liên Chi hội Nhà báo.",
    2: "Quyết định số 1119/QĐ-TNVN ngày 13/04/2026 tặng Bằng khen cho 09 tập thể.",
}
GOOD = "Ngày 16/01/2026, Đài ban hành Quyết định số 91/QĐ-TNVN tặng Bằng khen [1]."
WRONG = "Quyết định số 1119/QĐ-TNVN tặng Bằng khen cho 12 tập thể [2]."
TEXT = f"{GOOD} {WRONG}"


class Script:
    """Replays reviewer JSON replies and rewrite texts in order."""

    def __init__(self, reviews: list[Any], rewrites: list[str | None] | None = None) -> None:
        self.reviews = list(reviews)
        self.rewrites = list(rewrites or [])
        self.review_inputs: list[str] = []

    async def json_call(self, system: str, user: str, **_: Any) -> dict[str, Any] | None:
        self.review_inputs.append(user)
        return self.reviews.pop(0)

    async def text_call(self, system: str, user: str, **_: Any) -> str | None:
        return self.rewrites.pop(0) if self.rewrites else None

    def reviewer(self) -> SectionReviewer:
        return SectionReviewer(json_call=self.json_call, text_call=self.text_call)


ISSUE = {"sentence": WRONG, "problem": "contradiction", "explanation": "bằng chứng ghi 09"}


async def _run(script: Script, text: str = TEXT) -> tuple[str, Any]:
    return await script.reviewer().review_section(
        heading="II. Kết quả",
        purpose="Kết quả khen thưởng",
        style="hành chính",
        text=text,
        evidence=EVIDENCE,
        regate=lambda t: t,
    )


async def test_clean_section_is_kept() -> None:
    script = Script([{"issues": []}])
    text, review = await _run(script)
    assert text == TEXT and review.verdict == "ok"
    assert "[2] Quyết định số 1119" in script.review_inputs[0]  # evidence shown by number


async def test_flagged_section_is_rewritten_and_rechecked() -> None:
    fixed = f"{GOOD} Quyết định số 1119/QĐ-TNVN tặng Bằng khen cho 09 tập thể [2]."
    script = Script([{"issues": [ISSUE]}, {"issues": []}], rewrites=[fixed])
    text, review = await _run(script)
    assert text == fixed
    assert review.verdict == "revised" and review.issues[0].problem == "contradiction"


async def test_failed_rewrite_removes_the_flagged_sentence() -> None:
    script = Script([{"issues": [ISSUE]}], rewrites=[None])
    text, review = await _run(script)
    assert text == GOOD
    assert review.verdict == "trimmed" and review.removed_sentences == [WRONG]


async def test_sentence_still_wrong_after_rewrite_is_removed() -> None:
    still = "Quyết định số 1119/QĐ-TNVN tặng 12 tập thể [2]."
    script = Script(
        [{"issues": [ISSUE]}, {"issues": [{"sentence": still, "problem": "contradiction"}]}],
        rewrites=[f"{GOOD} {still}"],
    )
    text, review = await _run(script)
    assert text == GOOD and review.verdict == "trimmed"


async def test_rewrite_cannot_cite_evidence_it_was_not_given() -> None:
    script = Script(
        [{"issues": [ISSUE]}, {"issues": []}],
        rewrites=[f"{GOOD} Thêm một ý [9]. Ý đúng [2]."],
    )
    text, _review = await _run(script)
    assert "[9]" not in text and "[2]" in text


async def test_unavailable_reviewer_skips_without_changing_text() -> None:
    script = Script([None])
    text, review = await _run(script)
    assert text == TEXT and review.verdict == "skipped"


def test_remove_flagged_sentences_keeps_list_markers() -> None:
    text = f"- {GOOD}\n- {WRONG}\nĐoạn kết [1]."
    cleaned, removed = remove_flagged_sentences(text, [WRONG.replace(" [2]", "")])
    assert cleaned == f"- {GOOD}\nĐoạn kết [1]."
    assert removed == [WRONG]


def test_review_note_summarises_outcomes() -> None:
    assert review_note([{"review": None}]) == ""
    assert "không phát hiện" in review_note([{"review": {"verdict": "ok"}}])
    note = review_note(
        [
            {"review": {"verdict": "trimmed", "removed_sentences": ["a", "b"]}},
            {"review": {"verdict": "revised", "removed_sentences": []}},
        ]
    )
    assert "sửa lại 2 mục" in note and "loại 2 câu" in note
    assert "Chưa tự kiểm tra" in review_note([{"review": {"verdict": "skipped"}}])


async def test_at_most_three_reviews_then_trim() -> None:
    v2 = f"{GOOD} Quyết định số 1119/QĐ-TNVN tặng 11 tập thể [2]."
    v3 = f"{GOOD} Quyết định số 1119/QĐ-TNVN tặng 10 tập thể [2]."
    flag = {"issues": [{"sentence": "Quyết định số 1119/QĐ-TNVN tặng", "problem": "contradiction"}]}
    script = Script([{"issues": [ISSUE]}, flag, flag], rewrites=[v2, v3, "never used"])
    text, review = await _run(script)
    assert len(script.review_inputs) == 3  # never a fourth review
    assert script.rewrites == ["never used"]  # two rewrites at most
    assert text == GOOD and review.verdict == "trimmed" and review.rounds == 3


async def test_second_rewrite_can_fix_the_section() -> None:
    v2 = f"{GOOD} Quyết định số 1119/QĐ-TNVN tặng 11 tập thể [2]."
    fixed = f"{GOOD} Quyết định số 1119/QĐ-TNVN tặng Bằng khen cho 09 tập thể [2]."
    flag = {"issues": [{"sentence": v2.split(" [1]. ")[1], "problem": "contradiction"}]}
    script = Script([{"issues": [ISSUE]}, flag, {"issues": []}], rewrites=[v2, fixed])
    text, review = await _run(script)
    assert text == fixed and review.verdict == "revised" and review.rounds == 3


def test_dangling_opening_is_detected() -> None:
    from app.services.documents.review import structural_issues

    issues = structural_issues(
        "Thực trạng này đòi hỏi người mua phải có hiểu biết [1]. Câu sau [2]."
    )
    assert [i.problem for i in issues] == ["dangling_reference"]
    assert structural_issues("Thị trường thiết bị công nghệ đa dạng [1].") == []


async def test_dangling_opening_is_rewritten_never_removed() -> None:
    text = "Thực trạng này đòi hỏi người mua phải có hiểu biết [1]."
    fixed = "Thị trường điện thoại đa dạng khiến người mua cần hiểu biết [1]."
    script = Script([{"issues": []}, {"issues": []}], rewrites=[fixed])
    out, review = await _run(script, text)
    assert out == fixed and review.verdict == "revised"
    stuck = Script([{"issues": []}, {"issues": []}, {"issues": []}], rewrites=[text, text])
    out, review = await _run(stuck, text)
    assert out == text and review.removed_sentences == []  # kept, not deleted
