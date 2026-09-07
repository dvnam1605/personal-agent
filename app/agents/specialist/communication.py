"""CommunicationAgent domain layer (spec P12A).

Deterministic helpers around the P11 runtime for the CommunicationAgent:
system preamble, Direct task builders for the three covered read patterns,
contact-disambiguation reports, and fail-closed send proposals.

No LLM calls, no network, no tool execution here: these builders produce
:class:`SpecialistTask` / :class:`ProposedAction` values that the P11
``SpecialistRunner`` and the approval workflow consume.
"""

from __future__ import annotations

from app.agents.declarations import COMMUNICATION_AGENT_NAME
from app.core.sanitization import sanitize_string
from app.domain.enums import ActionRiskLevel, ExecutionMode, SpecialistStatus
from app.domain.errors import ValidationError
from app.domain.models import (
    ExecutionBudget,
    ProposedAction,
    SpecialistReport,
    SpecialistTask,
)

COMMUNICATION_AGENT = COMMUNICATION_AGENT_NAME

P12_REACT_BUDGET = ExecutionBudget(
    max_llm_calls=5,
    max_tool_calls=10,
    max_react_steps=4,
    max_prompt_tokens=3000,
    max_total_tokens=4000,
)

COMMUNICATION_SYSTEM_PREAMBLE: tuple[str, ...] = (
    "You are the CommunicationAgent: email triage, thread summarization, "
    "contact resolution, and draft composition over Gmail and Google Contacts.",
    "Exact lookups (latest email from a resolved contact, one thread by id, "
    "one contact by name) run DIRECT: call the single read tool, then report.",
    "When contact resolution returns more than one candidate, NEVER guess. "
    "Report needs_more_context listing every candidate name and email.",
    "Draft creation is non-destructive, but send, reply, and forward NEVER "
    "execute live. Return a ProposedAction with recipients, subject, "
    "sanitized body, and HIGH_IMPACT_WRITE risk for human approval.",
    "Mutation tools without an approval token fail closed; surface the "
    "PermissionDeniedError as a blocked report instead of retrying.",
)

PROPOSAL_BODY_PREVIEW_LEN = 4000
DISAMBIGUATION_CANDIDATE_LIMIT = 10


def latest_email_task(
    person_name: str,
    *,
    page_size: int = 1,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build the DIRECT task for "Email mới nhất của <người>" (spec P12 §3.2)."""
    name = _require_text(person_name, "person_name")
    return SpecialistTask(
        agent_name=COMMUNICATION_AGENT_NAME,
        goal=(
            f"Tìm email mới nhất của {name}: resolve contact '{name}' qua "
            "contacts.resolve_person, tìm bằng gmail.search_messages "
            f"(from:<email>, page_size={page_size}), đọc bằng gmail.get_message, "
            "rồi tóm tắt nội dung qua specialist.report."
        ),
        mode=ExecutionMode.DIRECT,
        context_data=dict(context_data or {}),
        system_preamble=COMMUNICATION_SYSTEM_PREAMBLE,
    )


def thread_read_task(
    thread_id: str,
    *,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build the DIRECT task for reading one thread by id."""
    identifier = _require_text(thread_id, "thread_id")
    return SpecialistTask(
        agent_name=COMMUNICATION_AGENT_NAME,
        goal=(
            f"Đọc toàn bộ hội thoại thread '{identifier}' bằng gmail.get_thread "
            "rồi tóm tắt diễn biến và quyết định cuối cùng qua specialist.report."
        ),
        mode=ExecutionMode.DIRECT,
        context_data=dict(context_data or {}),
        system_preamble=COMMUNICATION_SYSTEM_PREAMBLE,
    )


def contact_lookup_task(
    display_name: str,
    *,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build the DIRECT task for "Tìm người trong danh bạ"."""
    name = _require_text(display_name, "display_name")
    return SpecialistTask(
        agent_name=COMMUNICATION_AGENT_NAME,
        goal=(
            f"Tra cứu người '{name}' bằng contacts.resolve_person; "
            "một kết quả duy nhất thì báo cáo email và metadata, "
            "nhiều kết quả thì báo needs_more_context kèm toàn bộ ứng viên."
        ),
        mode=ExecutionMode.DIRECT,
        context_data=dict(context_data or {}),
        system_preamble=COMMUNICATION_SYSTEM_PREAMBLE,
    )


def complex_task(
    goal: str,
    *,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build a Bounded ReAct task capped by the P12 efficiency budget."""
    text = _require_text(goal, "goal")
    return SpecialistTask(
        agent_name=COMMUNICATION_AGENT_NAME,
        goal=text,
        mode=ExecutionMode.BOUNDED_REACT,
        context_data=dict(context_data or {}),
        budget=P12_REACT_BUDGET,
        system_preamble=COMMUNICATION_SYSTEM_PREAMBLE,
    )


def disambiguation_report(candidates: list[dict[str, str]]) -> SpecialistReport:
    """Map contact-resolution output to a clarification report (spec P12 §5).

    Zero candidates -> ask the user for a manual email (NEEDS_INPUT mapping).
    Multiple candidates -> list every name/email and request a selection.
    """
    if not candidates:
        return SpecialistReport(
            status=SpecialistStatus.NEEDS_MORE_CONTEXT,
            summary="Không tìm thấy liên hệ phù hợp. Vui lòng cung cấp địa chỉ email.",
            missing_context=["contact_email: manual email input required"],
        )
    listed = [
        f"{candidate.get('name', '?')} <{candidate.get('email', '?')}>"
        for candidate in candidates[:DISAMBIGUATION_CANDIDATE_LIMIT]
    ]
    return SpecialistReport(
        status=SpecialistStatus.NEEDS_MORE_CONTEXT,
        summary=("Tìm thấy nhiều liên hệ trùng tên, vui lòng chọn một: " + "; ".join(listed)),
        missing_context=listed,
    )


def build_send_proposal(
    *,
    recipients: list[str],
    subject: str,
    body: str,
    draft_id: str | None = None,
    reply_to_message_id: str | None = None,
    action_type: str = "send_email",
) -> ProposedAction:
    """Wrap a send/reply/forward intent as a fail-closed proposal (spec P12 §3.3)."""
    clean_recipients = [_require_text(recipient, "recipients[]") for recipient in recipients]
    if not clean_recipients:
        raise ValidationError(
            "Send proposal requires at least one recipient.",
            details={"action_type": action_type},
        )
    clean_subject = _require_text(subject, "subject")
    clean_body = _require_text(body, "body")
    preview = sanitize_string(clean_body, max_string_len=PROPOSAL_BODY_PREVIEW_LEN)
    parameters: dict[str, object] = {
        "to": clean_recipients,
        "subject": clean_subject,
        "body_preview": preview,
    }
    if draft_id is not None:
        parameters["draft_id"] = _require_text(draft_id, "draft_id")
    if reply_to_message_id is not None:
        parameters["reply_to_message_id"] = _require_text(
            reply_to_message_id, "reply_to_message_id"
        )
    return ProposedAction(
        action_type=action_type,
        description=(f"Gửi email tới {', '.join(clean_recipients)} với tiêu đề '{clean_subject}'."),
        tool_name="gmail.send_draft",
        parameters=parameters,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"CommunicationAgent input '{field}' must be a non-blank string.",
            details={"field": field},
        )
    return value.strip()


__all__ = [
    "COMMUNICATION_AGENT",
    "COMMUNICATION_SYSTEM_PREAMBLE",
    "DISAMBIGUATION_CANDIDATE_LIMIT",
    "P12_REACT_BUDGET",
    "PROPOSAL_BODY_PREVIEW_LEN",
    "build_send_proposal",
    "complex_task",
    "contact_lookup_task",
    "disambiguation_report",
    "latest_email_task",
    "thread_read_task",
]
