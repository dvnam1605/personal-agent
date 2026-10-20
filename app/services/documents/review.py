"""Self-review of a written section against the evidence it cites.

The deterministic gates (quality.py) catch figures that occur nowhere in the
evidence; they cannot see a claim attributed to the wrong document, a date
swapped between two decisions, or a sentence the evidence does not support.
After a section is written, a reviewer LLM checks every sentence against the
cited evidence:

1. no issue → the section is kept;
2. issues → the section is rewritten with the issues as instructions,
   passed through the gates again and re-reviewed, up to three reviews;
3. sentences still flagged by the last review (or when a rewrite fails) are
   removed, so a wrong statement never reaches the document.

When the gateway is down the review is skipped, never blocking the document.
Budget: one review per section; a section with issues costs at most two
rewrites and two more reviews.
"""

from __future__ import annotations

import difflib
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from app.core.text import unaccent_vietnamese
from app.services.documents.llm import llm_json, llm_text
from app.services.documents.quality import _LIST_MARKER, _sentences

logger = logging.getLogger(__name__)

REVIEW_TIMEOUT_SECONDS = 60.0
# Reviews per section; a section can be rewritten MAX_REVIEW_ROUNDS - 1 times.
MAX_REVIEW_ROUNDS = 3
MAX_EVIDENCE_CHARS = 1800  # per cited evidence unit shown to the reviewer
PROBLEMS = ("unsupported", "contradiction", "wrong_citation", "off_topic")

REVIEWER_SYSTEM_PROMPT = (
    "Bạn là chuyên viên thẩm định văn bản. Kiểm tra TỪNG CÂU của một mục tài liệu so với các "
    "bằng chứng được đánh số [1], [2], … mà câu đó trích dẫn.\n"
    "Chỉ báo lỗi về NỘI DUNG, thuộc một trong các loại:\n"
    "- unsupported: thông tin (sự kiện, đối tượng, nhận định cụ thể) không có trong bằng chứng;\n"
    "- contradiction: sai lệch với bằng chứng (số liệu, số tiền, ngày tháng, số hiệu văn bản, "
    "tên người, tên đơn vị, chức danh, quan hệ giữa văn bản);\n"
    "- wrong_citation: câu dẫn [n] nhưng bằng chứng [n] không chứa thông tin đó;\n"
    "- off_topic: câu không thuộc nội dung mục cần viết.\n"
    "Không bắt lỗi văn phong, cách diễn đạt hay câu nhận định khái quát hợp lý rút ra từ bằng "
    "chứng. Không báo lỗi khi không chắc chắn.\n"
    'Chỉ trả về JSON: {"issues": [{"sentence": "<chép nguyên văn câu lỗi>", '
    '"problem": "unsupported|contradiction|wrong_citation|off_topic", '
    '"explanation": "<sai ở đâu, bằng chứng nói gì>"}]}. Không có lỗi thì {"issues": []}.'
)

REVISER_SYSTEM_PROMPT = (
    "Bạn là chuyên viên soạn thảo văn bản. Viết lại MỘT mục tài liệu, sửa đúng các lỗi nội dung "
    "được người thẩm định chỉ ra, chỉ dựa trên các bằng chứng được đánh số [1], [2], ….\n"
    "1. Câu sai thì sửa theo bằng chứng; thông tin không có trong bằng chứng thì bỏ hẳn.\n"
    "2. Mọi số liệu, ngày tháng, số hiệu, tên người, tên đơn vị chép nguyên văn từ bằng chứng.\n"
    "3. Mỗi câu có thông tin cụ thể gắn nhãn trích dẫn đúng số của bằng chứng, dạng [2] hoặc "
    "[1][3]; chỉ dùng các số bằng chứng được cung cấp.\n"
    "4. Giữ nguyên các phần đúng, giữ văn phong và độ dài tương đương; văn xuôi, không tiêu đề "
    "con, không in đậm, không lời dẫn kiểu 'Dưới đây là…', không nhận xét về việc sửa.\n"
    "Chỉ trả về nội dung mục đã viết lại."
)


class ReviewIssue(BaseModel):
    sentence: str
    problem: str
    explanation: str = ""


class SectionReview(BaseModel):
    # ok: no issue | revised: rewritten, clean | trimmed: flagged sentences
    # removed | skipped: reviewer unavailable
    verdict: str
    issues: list[ReviewIssue] = Field(default_factory=list)
    removed_sentences: list[str] = Field(default_factory=list)
    rounds: int = 0


JsonCall = Callable[..., Awaitable[dict[str, Any] | None]]
TextCall = Callable[..., Awaitable[str | None]]


def _evidence_block(evidence: dict[int, str]) -> str:
    return "\n\n".join(
        f"[{n}] {text.strip()[:MAX_EVIDENCE_CHARS]}" for n, text in sorted(evidence.items())
    )


def _key(text: str) -> str:
    plain = re.sub(r"\[\d+\]", "", text)
    return re.sub(r"[\W_]+", "", unaccent_vietnamese(plain).lower())


def _matches(sentence: str, flagged: list[str]) -> bool:
    key = _key(sentence)
    if len(key) < 8:
        return False
    for target in flagged:
        other = _key(target)
        if len(other) < 8:
            continue
        if other in key or key in other:
            return True
        if difflib.SequenceMatcher(None, key, other).ratio() >= 0.85:
            return True
    return False


def remove_flagged_sentences(text: str, flagged: list[str]) -> tuple[str, list[str]]:
    """Drop every sentence matching a flagged one; list items keep their marker."""
    removed: list[str] = []
    out: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            out.append(line)
            continue
        marker = _LIST_MARKER.match(line)
        prefix = marker.group(0) if marker else ""
        kept: list[str] = []
        for sentence in _sentences(line[len(prefix) :]):
            if _matches(sentence, flagged):
                removed.append(sentence.strip())
            else:
                kept.append(sentence)
        if kept:
            out.append(prefix + " ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip(), removed


def _clean_citations(text: str, allowed: set[int]) -> str:
    """Remove [n] labels that point to no evidence the section had."""
    return re.sub(r"\[(\d+)\]", lambda m: m.group(0) if int(m.group(1)) in allowed else "", text)


class SectionReviewer:
    def __init__(self, *, json_call: JsonCall = llm_json, text_call: TextCall = llm_text) -> None:
        self._json = json_call
        self._text = text_call

    async def find_issues(
        self, heading: str, purpose: str, text: str, evidence: dict[int, str]
    ) -> list[ReviewIssue] | None:
        """Issues in ``text``; None when the reviewer could not run."""
        user = (
            f'Mục cần viết: "{heading}" — {purpose}\n\n'
            f"BẰNG CHỨNG:\n{_evidence_block(evidence)}\n\n"
            f"NỘI DUNG MỤC CẦN THẨM ĐỊNH:\n{text}"
        )
        data = await self._json(
            REVIEWER_SYSTEM_PROMPT, user, timeout=REVIEW_TIMEOUT_SECONDS, temperature=0.0
        )
        if data is None or not isinstance(data.get("issues"), list):
            return None
        issues: list[ReviewIssue] = []
        for raw in data["issues"]:
            if not isinstance(raw, dict) or not str(raw.get("sentence", "")).strip():
                continue
            problem = str(raw.get("problem", "")).strip()
            issues.append(
                ReviewIssue(
                    sentence=str(raw["sentence"]).strip()[:600],
                    problem=problem if problem in PROBLEMS else "unsupported",
                    explanation=str(raw.get("explanation", "")).strip()[:400],
                )
            )
        return issues

    async def rewrite(
        self,
        heading: str,
        purpose: str,
        style: str,
        text: str,
        issues: list[ReviewIssue],
        evidence: dict[int, str],
    ) -> str | None:
        problems = "\n".join(f'- "{i.sentence}" → {i.problem}: {i.explanation}' for i in issues)
        user = (
            f'Mục: "{heading}" — {purpose}\nVăn phong: {style}\n\n'
            f"BẰNG CHỨNG:\n{_evidence_block(evidence)}\n\n"
            f"BẢN ĐANG CÓ:\n{text}\n\nCÁC LỖI CẦN SỬA:\n{problems}"
        )
        revised = await self._text(REVISER_SYSTEM_PROMPT, user, timeout=REVIEW_TIMEOUT_SECONDS)
        if not revised:
            return None
        return _clean_citations(revised, set(evidence))

    async def review_section(
        self,
        *,
        heading: str,
        purpose: str,
        style: str,
        text: str,
        evidence: dict[int, str],
        regate: Callable[[str], str],
    ) -> tuple[str, SectionReview]:
        """Review → rewrite → review … up to MAX_REVIEW_ROUNDS reviews.

        Sentences still flagged by the last review (or when a rewrite fails)
        are removed. ``regate`` re-applies the deterministic quality gates to
        every rewrite.
        """
        if not evidence or not text.strip():
            return text, SectionReview(verdict="skipped")
        current = text
        all_issues: list[ReviewIssue] = []
        last: list[ReviewIssue] = []
        rounds = 0
        while rounds < MAX_REVIEW_ROUNDS:
            issues = await self.find_issues(heading, purpose, current, evidence)
            if issues is None:
                if rounds == 0:
                    return text, SectionReview(verdict="skipped")
                break  # reviewer failed after a rewrite: trim what was flagged last
            rounds += 1
            if not issues:
                verdict = "revised" if all_issues else "ok"
                return current, SectionReview(verdict=verdict, issues=all_issues, rounds=rounds)
            all_issues += issues
            last = issues
            if rounds == MAX_REVIEW_ROUNDS:
                break
            revised = await self.rewrite(heading, purpose, style, current, issues, evidence)
            revised = regate(revised) if revised else None
            if not revised or not re.search(r"\[\d+\]", revised):
                break
            current = revised
        cleaned, removed = remove_flagged_sentences(current, [i.sentence for i in last])
        return cleaned, SectionReview(
            verdict="trimmed", issues=all_issues, removed_sentences=removed, rounds=rounds
        )
