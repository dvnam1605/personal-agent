"""Prompt templates for answer synthesis (spec P10-18, P10-20).

Isolated from the synthesiser implementation so they are individually
testable and replaceable without touching orchestration code.
"""

from __future__ import annotations

from app.domain.models.retrieval import EvidenceBundle
from app.services.retrieval.injection_boundary import (
    BOUNDARY_INSTRUCTIONS,
    sanitize_evidence_for_prompt,
)

# ---------------------------------------------------------------------------
# System prompt — framing + injection boundary
# ---------------------------------------------------------------------------

SYNTHESIS_SYSTEM_PROMPT = (
    "You are a precise, evidence-based research assistant.  "
    "Answer the user's question using ONLY the retrieved evidence provided below.  "
    "Follow these rules strictly:\n\n"
    "1. Answer from evidence only — do NOT use prior knowledge or make up facts.\n"
    "2. Distinguish what the evidence states from your own inference.\n"
    "3. When evidence is insufficient, say so explicitly — never fabricate.\n"
    "4. Every material claim about a specific document must cite the evidence "
    "by its [evidence_id].\n"
    "5. If the evidence contains contradictions, note them.\n\n"
    f"{BOUNDARY_INSTRUCTIONS}\n"
)

SYNTHESIS_EXTERNAL_SYSTEM_PROMPT = (
    "You are a precise, evidence-based research assistant. "
    "Answer the user's question using the retrieved internal evidence provided below, "
    "supplemented by general external knowledge where appropriate. "
    "Follow these rules strictly:\n\n"
    "1. Clearly separate claims based on internal evidence from external knowledge.\n"
    "2. Cite internal evidence by its [evidence_id].\n"
    "3. Never contradict verified internal evidence with external assumptions.\n"
    "4. If internal evidence is missing or insufficient for certain parts, "
    "state that explicitly and label any supplemental external info clearly.\n\n"
    f"{BOUNDARY_INSTRUCTIONS}\n"
)

# ---------------------------------------------------------------------------
# User-message template
# ---------------------------------------------------------------------------

_QUESTION_HEADER = "## Question\n\n{question}\n\n## Retrieved Evidence\n\n"


def build_synthesis_user_message(
    question: str,
    bundle: EvidenceBundle,
    *,
    missing_documents: list[str] | None = None,
) -> str:
    """Compose the user-turn message with evidence items in boundary markers."""
    parts: list[str] = [_QUESTION_HEADER.format(question=question)]

    if missing_documents:
        parts.append(
            f"## Coverage Warning\n\n"
            f"Note: Evidence was not found for the following requested document(s): "
            f"{', '.join(missing_documents)}. "
            f"State clearly in your answer that these documents had no matching evidence.\n\n"
        )

    if not bundle.items:
        parts.append(
            "(No evidence was retrieved.  State that you cannot answer based "
            "on internal documents.)\n"
        )
    else:
        for idx, item in enumerate(bundle.items):
            parts.append(sanitize_evidence_for_prompt(item, index=idx))
            parts.append("")  # blank line separator

    parts.append(
        "\n## Instructions\n\n"
        "Answer the question above using the retrieved evidence.  "
        "Cite evidence by [evidence_id].  "
        "If the evidence is insufficient, clearly state that."
    )
    return "\n".join(parts)
