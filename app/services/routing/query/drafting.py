"""Turn a composed email into a reviewed draft, or a question when the recipient is unknown."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.services.critic import (
    EMAIL_RE,
    DraftCritic,
    EmailDraft,
    confirmed_recipients,
    fill_signature,
    valid_recipients,
)

from .models import QueryResult, QueryRouteInfo

RESUME_EMAIL_RECIPIENTS = "email_recipients"


@dataclass
class DraftPlan:
    """A draft ready for approval, or ``needs_recipients`` when the user must name them."""

    draft: EmailDraft
    needs_recipients: bool = False
    fixed: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)


async def prepare_draft(
    request: str,
    subject: str,
    body: str,
    recipients: list[str],
    *,
    personal: str = "",
    context: str = "",
    signature: str | None = None,
    critic: DraftCritic | None = None,
) -> DraftPlan:
    """Keep only recipients the user gave, fill the signature, and let the critic review.

    A recipient counts as given when its address appears in the request, in what the user
    asked the assistant to remember, or in the gathered context; an address the model
    made up is dropped. With no recipient left the caller asks instead of drafting, and
    the critic is not spent on a draft that will not be used.
    """
    draft = EmailDraft(subject=subject, body=body, recipients=valid_recipients(recipients))
    confirmed = confirmed_recipients(draft, request, personal, context)
    written = valid_recipients(EMAIL_RE.findall(request))
    draft = replace(draft, recipients=confirmed or written)
    draft = fill_signature(draft, signature)
    if not draft.recipients:
        return DraftPlan(draft=draft, needs_recipients=True)
    reviewed = await (critic or DraftCritic()).review(request, draft)
    first, last = reviewed.critiques[0], reviewed.critiques[-1]
    fixed = list(first.issues) if reviewed.revisions and reviewed.accepted else []
    remaining = [] if reviewed.accepted else list(last.issues)
    return DraftPlan(draft=reviewed.output, fixed=fixed, remaining=remaining)


def ask_recipients(run_id: str, route: QueryRouteInfo, request: str, subject: str) -> QueryResult:
    """Pause the request and ask who the email is for; the answer resumes it."""
    title = f"“{subject}” " if subject else ""
    return QueryResult(
        run_id=run_id,
        status="clarification_needed",
        message=(
            f"Bạn muốn gửi email {title}cho ai? Nhập địa chỉ email người nhận "
            "(nhiều người thì cách nhau bằng dấu phẩy), hoặc nói “hủy”."
        ),
        route=route,
        data={
            "resume": {"kind": RESUME_EMAIL_RECIPIENTS, "query": request},
            "suggested_choices": ["Hủy"],
        },
    )


def review_note(plan: DraftPlan) -> str:
    """One line telling the user what the critic fixed or still flags."""
    if plan.remaining:
        return "Lưu ý khi duyệt: " + "; ".join(plan.remaining)
    if plan.fixed:
        return "Đã tự rà soát và sửa: " + "; ".join(plan.fixed)
    return ""
