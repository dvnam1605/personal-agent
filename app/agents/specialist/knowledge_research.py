"""KnowledgeResearchAgent domain layer (spec P13).

Deterministic helpers around the P11 runtime for the KnowledgeResearchAgent:
system preamble, research-mode task builders, mixed-section composition, and
explicit no-answer reports.

No LLM calls, no network, no tool execution here: these builders produce
:class:`SpecialistTask` / :class:`SpecialistReport` values that the P11
``SpecialistRunner`` consumes. The agent ALWAYS runs under a read-only tool
view (spec P13 §4.1); this module never references a mutation tool.
"""

from __future__ import annotations

from enum import StrEnum

from app.agents.declarations import KNOWLEDGE_RESEARCH_AGENT_NAME
from app.domain.enums import ExecutionMode, SpecialistStatus
from app.domain.errors import ValidationError
from app.domain.models import (
    ExecutionBudget,
    SpecialistReport,
    SpecialistTask,
)
from app.services.retrieval.injection_boundary import BOUNDARY_INSTRUCTIONS

KNOWLEDGE_RESEARCH_AGENT = KNOWLEDGE_RESEARCH_AGENT_NAME


class ResearchMode(StrEnum):
    """P13 §3.1 research execution modes."""

    INTERNAL = "internal"
    WEB = "web"
    MIXED = "mixed"


INTERNAL_SECTION_HEADER = "[Tài liệu nội bộ]"
EXTERNAL_SECTION_HEADER = "[Nguồn mở rộng / Web]"

INSUFFICIENT_INTERNAL_MESSAGE = "Tôi không tìm thấy đủ tài liệu nội bộ để trả lời câu hỏi này."

REACT_BUDGET = ExecutionBudget(
    max_llm_calls=5,
    max_tool_calls=8,
    max_react_steps=6,
    max_prompt_tokens=4000,
    max_total_tokens=6000,
)

KNOWLEDGE_SYSTEM_PREAMBLE: tuple[str, ...] = (
    "You are the KnowledgeResearchAgent: evidence-based research over internal "
    "RAG documents, Google Drive files, and external web sources.",
    "INTERNAL mode uses retrieval.retrieve/synthesize and Drive read tools "
    "only, in one shot when the question is answerable directly. "
    "WEB mode answers explicitly external/current questions with web.search, "
    "labeling every claim with its source URL. "
    "MIXED mode compares internal evidence against external sources and MUST "
    f"structure the report into {INTERNAL_SECTION_HEADER} and "
    f"{EXTERNAL_SECTION_HEADER} sections.",
    "Every factual claim about a document MUST cite its [evidence_id]. "
    "When evidence is missing or insufficient, report the gap explicitly "
    "(INSUFFICIENT/PARTIAL) in Vietnamese — NEVER guess or hallucinate.",
    "You are strictly read-only: you hold no mutation tools and must never "
    "attempt writes, sends, deletes, or permission changes.",
    BOUNDARY_INSTRUCTIONS,
)


def internal_task(
    question: str,
    *,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build the DIRECT task for internal-only research (spec P13 §3.1 Mode 1)."""
    text = _require_text(question, "question")
    return SpecialistTask(
        agent_name=KNOWLEDGE_RESEARCH_AGENT_NAME,
        goal=(
            f"Trả lời câu hỏi nội bộ '{text}' bằng retrieval.synthesize "
            "(internal_only=true); không đủ bằng chứng thì báo "
            "INSUFFICIENT bằng tiếng Việt qua specialist.report."
        ),
        mode=ExecutionMode.DIRECT,
        context_data=dict(context_data or {}),
        system_preamble=KNOWLEDGE_SYSTEM_PREAMBLE,
    )


def web_task(
    question: str,
    *,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build the Bounded ReAct task for external/current research (Mode 2)."""
    text = _require_text(question, "question")
    return SpecialistTask(
        agent_name=KNOWLEDGE_RESEARCH_AGENT_NAME,
        goal=(
            f"Trả lời câu hỏi thời sự '{text}' bằng web.search; "
            "mọi khẳng định phải gắn URL nguồn qua specialist.report."
        ),
        mode=ExecutionMode.BOUNDED_REACT,
        context_data=dict(context_data or {}),
        budget=REACT_BUDGET,
        system_preamble=KNOWLEDGE_SYSTEM_PREAMBLE,
    )


def mixed_task(
    question: str,
    *,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build the Bounded ReAct task comparing internal vs external (Mode 3)."""
    text = _require_text(question, "question")
    return SpecialistTask(
        agent_name=KNOWLEDGE_RESEARCH_AGENT_NAME,
        goal=(
            f"Đối chiếu tài liệu nội bộ với nguồn bên ngoài cho '{text}': "
            "B1 retrieval.synthesize (internal_only=true), "
            "B2 web.search bổ sung, "
            f"B3 báo cáo 2 mục {INTERNAL_SECTION_HEADER} (claims kèm [evidence_id]) "
            f"và {EXTERNAL_SECTION_HEADER} (claims kèm URL) qua specialist.report."
        ),
        mode=ExecutionMode.BOUNDED_REACT,
        context_data=dict(context_data or {}),
        budget=REACT_BUDGET,
        system_preamble=KNOWLEDGE_SYSTEM_PREAMBLE,
    )


def research_task(
    question: str,
    mode: ResearchMode,
    *,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Dispatch to the builder for an explicit research mode."""
    if mode == ResearchMode.INTERNAL:
        return internal_task(question, context_data=context_data)
    if mode == ResearchMode.WEB:
        return web_task(question, context_data=context_data)
    if mode == ResearchMode.MIXED:
        return mixed_task(question, context_data=context_data)
    raise ValidationError(
        f"Unknown research mode: {mode!r}.",
        details={"mode": str(mode)},
    )


def complex_task(
    goal: str,
    *,
    context_data: dict | None = None,
) -> SpecialistTask:
    """Build a generic Bounded ReAct task capped by the P13 efficiency budget."""
    text = _require_text(goal, "goal")
    return SpecialistTask(
        agent_name=KNOWLEDGE_RESEARCH_AGENT_NAME,
        goal=text,
        mode=ExecutionMode.BOUNDED_REACT,
        context_data=dict(context_data or {}),
        budget=REACT_BUDGET,
        system_preamble=KNOWLEDGE_SYSTEM_PREAMBLE,
    )


def insufficient_report(
    answer: str = INSUFFICIENT_INTERNAL_MESSAGE,
) -> SpecialistReport:
    """Map an INSUFFICIENT outcome to an explicit Vietnamese no-answer report."""
    text = _require_text(answer, "answer")
    return SpecialistReport(
        status=SpecialistStatus.SUCCESS,
        summary=text,
        data={"sufficiency": "INSUFFICIENT", "answer": text, "citations": []},
    )


def mixed_report(
    internal_answer: str,
    external_answer: str,
    *,
    citations: list[dict[str, object]] | None = None,
) -> SpecialistReport:
    """Compose the dual-section mixed synthesis report (spec P13 §3 Mode 3)."""
    internal = _require_text(internal_answer, "internal_answer")
    external = _require_text(external_answer, "external_answer")
    summary = f"{INTERNAL_SECTION_HEADER}\n{internal}\n\n{EXTERNAL_SECTION_HEADER}\n{external}"
    return SpecialistReport(
        status=SpecialistStatus.SUCCESS,
        summary=summary,
        data={
            "sufficiency": "SUFFICIENT",
            "internal_answer": internal,
            "external_answer": external,
            "citations": list(citations or []),
        },
    )


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"KnowledgeResearchAgent input '{field}' must be a non-blank string.",
            details={"field": field},
        )
    return value.strip()


__all__ = [
    "EXTERNAL_SECTION_HEADER",
    "INTERNAL_SECTION_HEADER",
    "INSUFFICIENT_INTERNAL_MESSAGE",
    "KNOWLEDGE_RESEARCH_AGENT",
    "KNOWLEDGE_SYSTEM_PREAMBLE",
    "REACT_BUDGET",
    "ResearchMode",
    "complex_task",
    "insufficient_report",
    "internal_task",
    "mixed_report",
    "mixed_task",
    "research_task",
    "web_task",
]
