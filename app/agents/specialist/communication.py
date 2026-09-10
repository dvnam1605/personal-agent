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
    max_tool_calls=8,
    max_react_steps=6,
    max_prompt_tokens=3000,
    max_total_tokens=4000,
)

COMMUNICATION_SYSTEM_PREAMBLE: tuple[str, ...] = (
    "You are the CommunicationAgent: email triage, thread summarization, "
    "contact resolution, and draft composition over Gmail and Google Contacts.",
    "Exact lookups (latest email, one thread, one contact) run DIRECT: answer "
    "in a SINGLE turn from the pre-resolved context_data without tool calls. "
    "If context is missing, report needs_more_context — never guess. "
    "(Per P11-06, any tool call in DIRECT escalates to ReAct instead of "
    "executing, so multi-step lookups belong to BOUNDED_REACT tasks.)",
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
    """Build the DIRECT task for "Email mới nhất của <người>" (spec P12 §3.2).

    DIRECT answers in one turn from pre-resolved ``context_data`` (contact
    email / message payloads) and makes no tool calls; multi-step lookups
    belong to :func:`complex_task`.
    """
    name = _require_text(person_name, "person_name")
    size = _require_positive_int(page_size, "page_size")
    return SpecialistTask(
        agent_name=COMMUNICATION_AGENT_NAME,
        goal=(
            f"Tóm tắt email mới nhất của {name} (page_size={size}) DỰA VÀO "
            "context_data đã resolve sẵn, trong MỘT lượt duy nhất, KHÔNG gọi "
            "tool. Thiếu context thì báo needs_more_context, không đoán."
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
    """Build the DIRECT task for reading one thread from pre-loaded context."""
    identifier = _require_text(thread_id, "thread_id")
    return SpecialistTask(
        agent_name=COMMUNICATION_AGENT_NAME,
        goal=(
            f"Tóm tắt hội thoại thread '{identifier}' DỰA VÀO context_data "
            "có sẵn, trong MỘT lượt duy nhất, KHÔNG gọi tool. Thiếu context "
            "thì báo needs_more_context, không đoán."
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
    """Build the DIRECT task for answering from a pre-resolved contact."""
    name = _require_text(display_name, "display_name")
    return SpecialistTask(
        agent_name=COMMUNICATION_AGENT_NAME,
        goal=(
            f"Trình bày liên hệ '{name}' DỰA VÀO context_data đã resolve sẵn, "
            "trong MỘT lượt duy nhất, KHÔNG gọi tool. "
            "Nhiều ứng viên thì báo needs_more_context kèm toàn bộ danh sách."
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
    One candidate -> present the unique match (no ambiguity to resolve).
    Multiple candidates -> list every name/email and request a selection.
    """
    if not isinstance(candidates, list):
        raise ValidationError(
            "Disambiguation candidates must be a list of name/email mappings.",
            details={"received_type": type(candidates).__name__},
        )
    if not candidates:
        return SpecialistReport(
            status=SpecialistStatus.NEEDS_MORE_CONTEXT,
            summary="Không tìm thấy liên hệ phù hợp. Vui lòng cung cấp địa chỉ email.",
            missing_context=["contact_email: manual email input required"],
        )
    listed = [
        _format_candidate(candidate) for candidate in candidates[:DISAMBIGUATION_CANDIDATE_LIMIT]
    ]
    if len(candidates) == 1:
        return SpecialistReport(
            status=SpecialistStatus.SUCCESS,
            summary=f"Đã xác định được một liên hệ duy nhất: {listed[0]}.",
            data={"contact": listed[0]},
        )
    return SpecialistReport(
        status=SpecialistStatus.NEEDS_MORE_CONTEXT,
        summary=("Tìm thấy nhiều liên hệ trùng tên, vui lòng chọn một: " + "; ".join(listed)),
        missing_context=listed,
    )


def _format_candidate(candidate: object) -> str:
    if not isinstance(candidate, dict):
        raise ValidationError(
            "Disambiguation candidates must be name/email mappings.",
            details={"received_type": type(candidate).__name__},
        )
    name = candidate.get("name", "?")
    email = candidate.get("email", "?")
    return f"{name} <{email}>"


_SEND_ACTION_TOOLS = {
    "send_email": "gmail.send_draft",
    "reply": "gmail.reply",
    "forward": "gmail.forward",
}


def build_send_proposal(
    *,
    recipients: list[str],
    subject: str,
    body: str,
    draft_id: str | None = None,
    message_id: str | None = None,
    action_type: str = "send_email",
) -> ProposedAction:
    """Wrap a send/reply/forward intent as a fail-closed proposal (spec P12 §3.3).

    The proposal targets the real mutation tool for the intent: ``send_email``
    creates-then-sends via ``gmail.send_draft`` (``draft_id`` when a draft
    already exists), while ``reply``/``forward`` target ``gmail.reply`` /
    ``gmail.forward`` and require the source ``message_id``.
    """
    clean_recipients = _require_str_list(recipients, "recipients")
    if not clean_recipients:
        raise ValidationError(
            "Send proposal requires at least one recipient.",
            details={"action_type": action_type},
        )
    clean_subject = _require_text(subject, "subject")
    clean_body = _require_text(body, "body")
    try:
        tool_name = _SEND_ACTION_TOOLS[action_type]
    except (KeyError, TypeError) as exc:
        raise ValidationError(
            f"Unknown send action type: {action_type!r}.",
            details={"action_type": str(action_type)},
        ) from exc
    preview = sanitize_string(clean_body, max_string_len=PROPOSAL_BODY_PREVIEW_LEN)
    parameters: dict[str, object] = {
        "to": clean_recipients,
        "subject": clean_subject,
        "body_preview": preview,
    }
    if draft_id is not None:
        parameters["draft_id"] = _require_text(draft_id, "draft_id")
    if action_type in ("reply", "forward"):
        parameters["message_id"] = _require_text(message_id or "", "message_id")
    action_verb = {"send_email": "Gửi email", "reply": "Trả lời", "forward": "Chuyển tiếp"}[
        action_type
    ]
    return ProposedAction(
        action_type=action_type,
        description=(
            f"{action_verb} tới {', '.join(clean_recipients)} với tiêu đề '{clean_subject}'."
        ),
        tool_name=tool_name,
        parameters=parameters,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        requires_approval=True,
    )


_MESSAGE_ACTION_TOOLS = {
    "archive": "gmail.archive",
    "trash": "gmail.trash",
    "delete_draft": "gmail.delete_draft",
}

_MESSAGE_ACTION_DESCRIPTIONS = {
    "archive": "Lưu trữ thư",
    "trash": "Chuyển thư vào thùng rác",
    "delete_draft": "Xóa bản nháp",
}


def build_message_action_proposal(
    *,
    action_type: str,
    message_id: str | None = None,
    draft_id: str | None = None,
) -> ProposedAction:
    """Wrap an email management intent (archive, trash, delete_draft) as a fail-closed proposal (spec P12 §3.1)."""
    try:
        tool_name = _MESSAGE_ACTION_TOOLS[action_type]
        action_verb = _MESSAGE_ACTION_DESCRIPTIONS[action_type]
    except (KeyError, TypeError) as exc:
        raise ValidationError(
            f"Unknown message action type: {action_type!r}.",
            details={"action_type": str(action_type)},
        ) from exc

    parameters: dict[str, object] = {}
    if action_type in ("archive", "trash"):
        parameters["message_id"] = _require_text(message_id or "", "message_id")
        target_id = parameters["message_id"]
        risk_level = (
            ActionRiskLevel.IRREVERSIBLE
            if action_type == "trash"
            else ActionRiskLevel.LOW_IMPACT_WRITE
        )
    else:  # delete_draft
        parameters["draft_id"] = _require_text(draft_id or "", "draft_id")
        target_id = parameters["draft_id"]
        risk_level = ActionRiskLevel.IRREVERSIBLE

    return ProposedAction(
        action_type=action_type,
        description=f"{action_verb} '{target_id}'.",
        tool_name=tool_name,
        parameters=parameters,
        risk_level=risk_level,
        requires_approval=True,
    )


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"CommunicationAgent input '{field}' must be a non-blank string.",
            details={"field": field},
        )
    return value.strip()


def _require_str_list(value: object, field: str) -> list[str]:
    if isinstance(value, str) or not isinstance(value, list):
        raise ValidationError(
            f"CommunicationAgent input '{field}' must be a list of non-blank strings.",
            details={"field": field, "received_type": type(value).__name__},
        )
    return [_require_text(item, f"{field}[]") for item in value]


def _require_positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValidationError(
            f"CommunicationAgent input '{field}' must be a positive integer.",
            details={"field": field},
        )
    if isinstance(value, float):
        if not value.is_integer():
            raise ValidationError(
                f"CommunicationAgent input '{field}' must be a positive integer.",
                details={"field": field},
            )
        value = int(value)
    if not isinstance(value, int) or value < 1:
        raise ValidationError(
            f"CommunicationAgent input '{field}' must be a positive integer.",
            details={"field": field},
        )
    return value


__all__ = [
    "COMMUNICATION_AGENT",
    "COMMUNICATION_SYSTEM_PREAMBLE",
    "DISAMBIGUATION_CANDIDATE_LIMIT",
    "P12_REACT_BUDGET",
    "PROPOSAL_BODY_PREVIEW_LEN",
    "build_message_action_proposal",
    "build_send_proposal",
    "complex_task",
    "contact_lookup_task",
    "disambiguation_report",
    "latest_email_task",
    "thread_read_task",
]
