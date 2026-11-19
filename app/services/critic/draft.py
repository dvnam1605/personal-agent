"""Critic for email drafts: deterministic checks first, then an optional model review."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.core.llm import llm_json
from app.services.critic.loop import Critique, EvaluatorOptimizer, ReviewedOutput

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PLACEHOLDER_RE = re.compile(
    r"\[(?:tên|ten|họ tên|chữ ký|chu ky|người nhận|nguoi nhan)[^\]]*\]", re.I
)
MIN_BODY_LEN = 40

CRITIC_SYSTEM = (
    "Bạn là người rà soát email công việc, độc lập với người soạn. Kiểm tra bản nháp so với "
    "yêu cầu: đúng mục đích, đủ ý, lịch sự, không bịa số liệu hay cam kết không có trong yêu "
    "cầu, không còn chỗ trống dạng [..]. Trả về JSON duy nhất: "
    '{"ok": true|false, "issues": ["vấn đề ngắn gọn", ...]}.'
)
REVISE_SYSTEM = (
    "Bạn sửa bản nháp email theo các vấn đề người rà soát nêu. Giữ nguyên người nhận và ý "
    "chính, chỉ sửa phần có vấn đề. Trả về JSON duy nhất: "
    '{"subject": "...", "body": "..."}.'
)


@dataclass(frozen=True)
class EmailDraft:
    """The parts of an email the critic reviews."""

    subject: str
    body: str
    recipients: tuple[str, ...]


def valid_recipients(recipients: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Keep only well-formed mailbox addresses, lower-cased and de-duplicated."""
    seen: list[str] = []
    for value in recipients:
        match = EMAIL_RE.fullmatch(str(value).strip())
        if match and match.group(0).lower() not in seen:
            seen.append(match.group(0).lower())
    return tuple(seen)


def confirmed_recipients(draft: EmailDraft, *sources: str) -> tuple[str, ...]:
    """Recipients the user actually gave: written in the request, memory or context.

    An address the model produced on its own is a guess; sending a draft to a guessed
    person is worse than asking, so unconfirmed addresses are dropped.
    """
    known = {address.lower() for source in sources for address in EMAIL_RE.findall(source or "")}
    return tuple(address for address in draft.recipients if address in known)


def fill_signature(draft: EmailDraft, signature: str | None) -> EmailDraft:
    """Replace a "[Tên của bạn]"-style placeholder with the remembered signature."""
    if not signature or not PLACEHOLDER_RE.search(draft.body):
        return draft
    return replace(draft, body=PLACEHOLDER_RE.sub(signature, draft.body))


def structural_issues(draft: EmailDraft) -> list[str]:
    """Problems found without a model: missing parts and unfilled placeholders."""
    issues: list[str] = []
    if not draft.subject.strip():
        issues.append("Thiếu tiêu đề.")
    if len(draft.body.strip()) < MIN_BODY_LEN:
        issues.append("Nội dung quá ngắn.")
    if PLACEHOLDER_RE.search(draft.body):
        issues.append("Còn chỗ trống chưa điền (ví dụ [Tên của bạn]).")
    return issues


class DraftCritic:
    """Review a draft against the request and revise it at most ``max_revisions`` times.

    Structural checks always run; the model critique and revision run only when an LLM is
    configured (``llm_json`` returns ``None`` otherwise), so the critic never blocks a
    draft on an unavailable gateway.
    """

    def __init__(self, *, max_revisions: int = 1) -> None:
        self._max_revisions = max_revisions

    async def review(self, request: str, draft: EmailDraft) -> ReviewedOutput[EmailDraft]:
        async def critique(current: EmailDraft) -> Critique:
            issues = structural_issues(current)
            verdict = await llm_json(CRITIC_SYSTEM, _critic_prompt(request, current), timeout=30.0)
            if isinstance(verdict, dict):
                issues.extend(str(item) for item in list(verdict.get("issues") or [])[:5])
                model_ok = bool(verdict.get("ok"))
            else:
                model_ok = True
            return Critique(ok=model_ok and not issues, issues=issues)

        async def revise(current: EmailDraft, verdict: Critique) -> EmailDraft | None:
            prompt = (
                _critic_prompt(request, current)
                + "\n\nVấn đề cần sửa:\n"
                + "\n".join(f"- {issue}" for issue in verdict.issues)
            )
            fixed = await llm_json(REVISE_SYSTEM, prompt, timeout=40.0)
            if not isinstance(fixed, dict) or not str(fixed.get("body") or "").strip():
                return None
            return replace(
                current,
                subject=str(fixed.get("subject") or current.subject).strip(),
                body=str(fixed["body"]).strip(),
            )

        loop = EvaluatorOptimizer(critique, revise, max_revisions=self._max_revisions)
        return await loop.run(draft)


def _critic_prompt(request: str, draft: EmailDraft) -> str:
    return (
        f"Yêu cầu của người dùng: {request}\n\n"
        f"Người nhận: {', '.join(draft.recipients) or '(chưa có)'}\n"
        f"Tiêu đề: {draft.subject}\n\nNội dung:\n{draft.body}"
    )
