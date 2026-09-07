"""Answer synthesis separated from retrieval (spec P10-18 + P10-19).

The ``AnswerSynthesizer`` protocol defines the seam; the default
``PromptAnswerSynthesizer`` builds a structured prompt with evidence
framed as untrusted data (P10-20) and extracts citations from the model
response.

The protocol is async to support LLM backends; callers that only need the
prompt-building logic (tests, offline evaluation) can use
``build_synthesis_messages`` directly.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.citation import Citation
from app.domain.models.retrieval import EvidenceBundle
from app.domain.models.sufficiency import SufficiencyStatus

logger = logging.getLogger(__name__)


class SynthesisResult(BaseModel):
    """Structured output from answer synthesis (spec P10-18)."""

    model_config = ConfigDict(frozen=True)

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    status: SufficiencyStatus = SufficiencyStatus.SUFFICIENT
    confidence: float | None = None
    synthesis_trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


@runtime_checkable
class AnswerSynthesizer(Protocol):
    """Pluggable synthesis seam (spec P10-18)."""

    async def synthesize(
        self,
        question: str,
        bundle: EvidenceBundle,
        *,
        internal_only: bool = True,
        missing_documents: list[str] | None = None,
        verdict_status: SufficiencyStatus = SufficiencyStatus.SUFFICIENT,
    ) -> SynthesisResult: ...


# ---------------------------------------------------------------------------
# Citation extraction helpers
# ---------------------------------------------------------------------------

_CITE_RE = re.compile(r"\[([0-9a-fA-F]{32})\]")


def extract_cited_ids(text: str) -> list[str]:
    """Pull unique evidence_id hex strings from ``[<hex32>]`` markers."""
    return list(dict.fromkeys(_CITE_RE.findall(text)))


def build_citations_from_bundle(
    cited_ids: list[str],
    bundle: EvidenceBundle,
) -> list[Citation]:
    """Map cited evidence ids back to ``Citation`` objects using bundle items."""
    id_to_item = {item.evidence_id: item for item in bundle.items}
    citations: list[Citation] = []
    for eid in cited_ids:
        item = id_to_item.get(eid)
        if item is None:
            logger.debug("cited_evidence_not_in_bundle", extra={"evidence_id": eid})
            continue
        title = item.title or item.anchors.get("document_title") or ""
        source_type = item.source_type or item.anchors.get("source_type") or ""
        citations.append(
            Citation(
                evidence_id=item.evidence_id,
                document_id=item.document_id,
                title=title,
                page_start=item.anchors.get("page_start"),
                page_end=item.anchors.get("page_end"),
                heading_path=list(item.heading_path),
                source_type=source_type,
            )
        )
    return citations


# ---------------------------------------------------------------------------
# Default implementation
# ---------------------------------------------------------------------------


class PromptAnswerSynthesizer:
    """Default synthesis using a structured prompt (P10-18).

    This implementation builds the prompt messages and delegates to a
    caller-supplied ``generate`` callback for the actual LLM call, keeping
    the synthesiser decoupled from any specific model client.
    """

    def __init__(
        self,
        generate: GenerateCallback | None = None,
    ) -> None:
        if generate is not None:
            self._generate = generate
        else:
            self._generate = self._build_default_generate() or _stub_generate

    @staticmethod
    def _build_default_generate() -> GenerateCallback | None:
        try:
            from app.core.config import settings

            api_key = settings.llm.openai_api_key
            if not api_key:
                return None
            import httpx

            async def _openai_generate(system: str, user: str) -> str:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": settings.llm.primary_model,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            "temperature": settings.llm.temperature,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return str(data["choices"][0]["message"]["content"])

            return _openai_generate
        except Exception:
            return None

    async def synthesize(
        self,
        question: str,
        bundle: EvidenceBundle,
        *,
        internal_only: bool = True,
        missing_documents: list[str] | None = None,
        verdict_status: SufficiencyStatus = SufficiencyStatus.SUFFICIENT,
    ) -> SynthesisResult:
        from app.services.retrieval.prompts import (
            SYNTHESIS_EXTERNAL_SYSTEM_PROMPT,
            SYNTHESIS_SYSTEM_PROMPT,
            build_synthesis_user_message,
        )

        if not bundle.items:
            return SynthesisResult(
                answer=(
                    "Tôi không tìm thấy tài liệu nội bộ nào phù hợp để trả lời câu hỏi này."
                    if internal_only
                    else "Không có tài liệu hoặc thông tin phù hợp để trả lời câu hỏi này."
                ),
                status=SufficiencyStatus.INSUFFICIENT,
                citations=[],
            )

        system_msg = SYNTHESIS_SYSTEM_PROMPT if internal_only else SYNTHESIS_EXTERNAL_SYSTEM_PROMPT
        user_msg = build_synthesis_user_message(
            question, bundle, missing_documents=missing_documents
        )

        answer_text = await self._generate(system_msg, user_msg)

        cited_ids = extract_cited_ids(answer_text)
        citations = build_citations_from_bundle(cited_ids, bundle)

        return SynthesisResult(
            answer=answer_text,
            citations=citations,
            status=verdict_status,
        )


GenerateCallback = Callable[[str, str], Awaitable[str]]


async def _stub_generate(system: str, user: str) -> str:
    """Placeholder that returns a no-answer — callers must inject a real LLM."""
    logger.warning(
        "synthesis_stub_generate_invoked",
        extra={"hint": "inject a real LLM generate callback in production"},
    )
    return (
        "Answer synthesis is not yet configured.  "
        "Please provide a generate callback to PromptAnswerSynthesizer."
    )
