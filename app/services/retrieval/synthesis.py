"""Answer synthesis separated from retrieval (spec P10-18 + P10-19).

The ``AnswerSynthesizer`` protocol defines the seam; the default
``PromptAnswerSynthesizer`` builds a structured prompt with evidence
framed as untrusted data (P10-20) and extracts citations from the model
response.

The protocol is async to support LLM backends; callers that only need the
prompt-building logic (tests, offline evaluation) can use
``build_synthesis_messages`` directly. The default LLM callbacks live in
``synthesis_llm`` and citation building in ``synthesis_citations``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.images import strip_images
from app.domain.models.retrieval import EvidenceBundle
from app.domain.models.retrieval.citation import Citation
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.services.retrieval.citations import (
    REFUSAL_RE,
    CitationNumberer,
    evidence_snapshot,
    strip_orphan_ids,
)
from app.services.retrieval.grounding import GroundingReport, check_answer
from app.services.retrieval.images import (
    MARKER,
    ImageMarkers,
    ImageRefs,
    interleave,
    is_step_by_step,
    refs_in,
)
from app.services.retrieval.prompts import (
    SYNTHESIS_EXTERNAL_SYSTEM_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
    build_synthesis_user_message,
)
from app.services.retrieval.synthesis_citations import (
    build_citations_from_bundle,
    extract_cited_ids,
    format_document_title,
)
from app.services.retrieval.synthesis_llm import (
    GenerateCallback,
    StreamGenerateCallback,
    SynthesisInterruptedError,
    build_default_generate,
    build_default_stream_generate,
    unconfigured_generate,
    unconfigured_stream_generate,
)

__all__ = [
    "AnswerSynthesizer",
    "GenerateCallback",
    "PromptAnswerSynthesizer",
    "StreamGenerateCallback",
    "SynthesisInterruptedError",
    "SynthesisResult",
    "build_citations_from_bundle",
    "extract_cited_ids",
    "format_document_title",
]

logger = logging.getLogger(__name__)


class SynthesisResult(BaseModel):
    """Structured output from answer synthesis (spec P10-18)."""

    model_config = ConfigDict(frozen=True)

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    status: SufficiencyStatus = SufficiencyStatus.SUFFICIENT
    confidence: float | None = None
    synthesis_trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    # Sentences stating data (amounts, dates, %) without a citation badge.
    unsupported_claims: list[str] = Field(default_factory=list)
    # Server-side snapshot of every evidence unit shown to the model (P2).
    retrieval_trace_id: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


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
        conversation_history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
    ) -> SynthesisResult: ...

    def synthesize_stream(
        self,
        question: str,
        bundle: EvidenceBundle,
        *,
        internal_only: bool = True,
        missing_documents: list[str] | None = None,
        verdict_status: SufficiencyStatus = SufficiencyStatus.SUFFICIENT,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]: ...


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
        stream_generate: StreamGenerateCallback | None = None,
    ) -> None:
        if generate is not None:
            self._generate = generate
        else:
            self._generate = build_default_generate() or unconfigured_generate

        if stream_generate is not None:
            self._stream_generate = stream_generate
        else:
            self._stream_generate = build_default_stream_generate() or unconfigured_stream_generate

    async def synthesize(
        self,
        question: str,
        bundle: EvidenceBundle,
        *,
        internal_only: bool = True,
        missing_documents: list[str] | None = None,
        verdict_status: SufficiencyStatus = SufficiencyStatus.SUFFICIENT,
        conversation_history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
        extra_images: str = "",
    ) -> SynthesisResult:
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

        # Callers with their own task (e.g. document writing) supply the prompt.
        system_msg = system_prompt or (
            SYNTHESIS_SYSTEM_PROMPT if internal_only else SYNTHESIS_EXTERNAL_SYSTEM_PROMPT
        )
        user_msg = build_synthesis_user_message(
            question,
            bundle,
            missing_documents=missing_documents,
            conversation_history=conversation_history,
        )

        interrupted = False
        try:
            answer_text = await self._generate(system_msg, user_msg)
        except SynthesisInterruptedError as cut:
            logger.warning("synthesis_generate_interrupted chars=%d", len(cut.partial))
            answer_text = cut.partial
            interrupted = True
        except Exception as gen_err:  # noqa: BLE001 - fallback to raw evidence on LLM failure
            # Error details stay in the log; the user gets a stable message.
            logger.error("synthesis_generate_failed error=%s", gen_err)
            fallback_eids = [it.evidence_id for it in bundle.items[:3]]
            citations = build_citations_from_bundle(fallback_eids, bundle)
            return SynthesisResult(
                answer=(
                    "⚠️ Không thể kết nối đến mô hình AI để tạo tóm tắt chi tiết. "
                    "Dưới đây là các tài liệu liên quan được trích xuất từ kho tri thức:"
                ),
                citations=citations,
                status=SufficiencyStatus.PARTIAL if citations else SufficiencyStatus.INSUFFICIENT,
                retrieval_trace_id=bundle.retrieval_trace_id,
                evidence=evidence_snapshot(bundle),
            )

        numberer = CitationNumberer(bundle)
        markers = _image_markers(bundle, extra_images)
        cleaned_answer = markers.replace(numberer.rewrite(answer_text), numberer.cited_ids)
        # Only what the model actually cited — never a stand-in "top 3".
        citations = build_citations_from_bundle(numberer.cited_ids, bundle)
        status = _uncited_status(strip_images(cleaned_answer), citations, verdict_status)
        cleaned_answer = _with_fallback_images(cleaned_answer, markers, bundle, numberer)
        if interrupted:
            return SynthesisResult(
                answer=cleaned_answer + _INTERRUPTED_NOTE,
                citations=citations,
                status=SufficiencyStatus.PARTIAL,
                retrieval_trace_id=bundle.retrieval_trace_id,
                evidence=evidence_snapshot(bundle, numberer),
            )
        if not citations and status is SufficiencyStatus.PARTIAL:
            cleaned_answer += _UNCITED_NOTE
        grounding = check_answer(strip_images(cleaned_answer))
        return SynthesisResult(
            answer=cleaned_answer,
            citations=citations,
            status=_grounded_status(status, grounding),
            unsupported_claims=grounding.unsupported,
            retrieval_trace_id=bundle.retrieval_trace_id,
            evidence=evidence_snapshot(bundle, numberer),
        )

    async def synthesize_stream(
        self,
        question: str,
        bundle: EvidenceBundle,
        *,
        internal_only: bool = True,
        missing_documents: list[str] | None = None,
        verdict_status: SufficiencyStatus = SufficiencyStatus.SUFFICIENT,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream token-by-token synthesis from LLM, then emit citations.

        Yields dicts with:
        - `{"type": "token", "delta": "..."}`
        - `{"type": "citations", "citations": [...], "status": "..."}`
        """
        if not bundle.items:
            empty_msg = (
                "Tôi không tìm thấy tài liệu nội bộ nào phù hợp để trả lời câu hỏi này."
                if internal_only
                else "Không có tài liệu hoặc thông tin phù hợp để trả lời câu hỏi này."
            )
            yield {"type": "token", "delta": empty_msg}
            yield {
                "type": "citations",
                "citations": [],
                "status": SufficiencyStatus.INSUFFICIENT.value,
            }
            return

        system_msg = SYNTHESIS_SYSTEM_PROMPT if internal_only else SYNTHESIS_EXTERNAL_SYSTEM_PROMPT
        user_msg = build_synthesis_user_message(
            question,
            bundle,
            missing_documents=missing_documents,
            conversation_history=conversation_history,
        )

        # Same numberer as the non-streaming path: identical [n] badges, and
        # labels absent from the bundle are dropped instead of numbered.
        numberer = CitationNumberer(bundle)
        markers = _image_markers(bundle)
        streamed: list[str] = []
        buf = ""

        def _process_buffer(force: bool = False) -> str:
            nonlocal buf
            out = ""
            while buf:
                bracket_pos = buf.find("[")
                if bracket_pos == -1:
                    out += strip_orphan_ids(buf)
                    buf = ""
                    break
                if bracket_pos > 0:
                    out += strip_orphan_ids(buf[:bracket_pos])
                    buf = buf[bracket_pos:]
                    continue

                # buf starts with '['
                close_pos = buf.find("]")
                if close_pos != -1:
                    tag = buf[: close_pos + 1]
                    if marker := MARKER.fullmatch(tag):
                        if line := markers.resolve(marker.group(1), numberer.cited_ids):
                            out += f"\n{line}\n"
                    elif numberer.looks_like_citation(tag):
                        out += numberer.render_bracket(tag)
                    elif not numberer.mentions_evidence(tag):
                        out += tag
                    # else: a malformed tag carrying evidence ids -> dropped
                    buf = buf[close_pos + 1 :]
                    continue

                # No closing ']' yet
                if force:
                    if not numberer.mentions_evidence(buf):
                        out += buf
                    buf = ""
                    break
                if "\n" in buf or len(buf) > 300:
                    out += buf[0]
                    buf = buf[1:]
                    continue

                # Need more characters to determine
                break
            return out

        status = verdict_status
        emitted_any = False
        # Per-read httpx timeouts never fire on a slow-trickling stream, so the
        # whole generation also gets a wall-clock deadline (checked per chunk:
        # an asyncio.timeout scope must not span this generator's yields).
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.timeouts.llm_request_seconds
        failure: str | None = None
        try:
            # aclosing: stopping at the deadline must also close the HTTP stream.
            async with contextlib.aclosing(self._stream_generate(system_msg, user_msg)) as stream:
                async for chunk in stream:
                    buf += chunk
                    emitted = _process_buffer(force=False)
                    if emitted:
                        emitted_any = True
                        streamed.append(emitted)
                        yield {"type": "token", "delta": emitted}
                    if loop.time() > deadline:
                        failure = "deadline"
                        break
        except Exception as stream_err:  # noqa: BLE001 - fallback gracefully on LLM stream failure
            logger.error("synthesis_stream_generate_failed error=%s", stream_err)
            failure = "error"

        # Flush any remaining buffer
        final_emitted = _process_buffer(force=True)
        if final_emitted:
            emitted_any = True
            streamed.append(final_emitted)
            yield {"type": "token", "delta": final_emitted}

        if failure is not None:
            logger.warning("synthesis_stream_incomplete reason=%s", failure)
            status = SufficiencyStatus.PARTIAL
            yield {
                "type": "token",
                "delta": _INTERRUPTED_NOTE
                if emitted_any
                else (
                    "⚠️ Không thể kết nối đến mô hình AI để tạo tóm tắt chi tiết. "
                    "Dưới đây là các tài liệu liên quan được trích xuất từ kho tri thức:\n"
                ),
            }

        citations = build_citations_from_bundle(numberer.cited_ids, bundle)
        grounding = check_answer(strip_images("".join(streamed)))
        if failure is None:
            status = _uncited_status("".join(streamed), citations, status)
            if not citations and status is SufficiencyStatus.PARTIAL:
                yield {"type": "token", "delta": _UNCITED_NOTE}
            status = _grounded_status(status, grounding)

        # A step-by-step answer the model left without image markers gets its
        # images placed afterwards; the client swaps the streamed text for it.
        text = "".join(streamed)
        with_images = _with_fallback_images(text, markers, bundle, numberer)
        yield {
            "type": "citations",
            "citations": [c.model_dump(mode="json") for c in citations],
            **({"answer": with_images} if with_images != text else {}),
            "status": status.value,
            "unsupported_claims": grounding.unsupported,
            "retrieval_trace_id": bundle.retrieval_trace_id,
            "evidence": evidence_snapshot(bundle, numberer),
        }


def _image_markers(bundle: EvidenceBundle, extra_images: str = "") -> ImageMarkers:
    """Markers over the evidence images, then any extra ones the caller offers."""
    evidence = [(item.evidence_id, item.content_raw or "") for item in bundle.items]
    return ImageMarkers([*evidence, ("extra", extra_images)] if extra_images else evidence)


def _with_fallback_images(
    text: str, markers: ImageMarkers, bundle: EvidenceBundle, numberer: CitationNumberer
) -> str:
    """``text``, or with images aligned to its steps when the model placed none."""
    if markers.shown or not is_step_by_step(text):
        return text
    return interleave(text, _images_lookup(bundle, numberer))


def _images_lookup(
    bundle: EvidenceBundle, numberer: CitationNumberer
) -> Callable[[int], ImageRefs]:
    """Images of the evidence behind citation number ``n``, in source order."""
    by_eid = {item.evidence_id: refs_in(item.content_raw or "") for item in bundle.items}
    return lambda n: by_eid.get(numberer.evidence_id_of(n) or "", [])


_UNCITED_NOTE = (
    "\n\n_(Câu trả lời này chưa gắn được trích dẫn tới tài liệu nguồn; "
    "hãy đối chiếu lại với tài liệu trước khi sử dụng.)_"
)


def _uncited_status(
    answer: str, citations: list[Citation], verdict: SufficiencyStatus
) -> SufficiencyStatus:
    """An answer with no real citation is never SUFFICIENT.

    A refusal ("không tìm thấy …") is INSUFFICIENT; any other uncited answer
    is PARTIAL and gets a visible note rather than borrowed citations.
    """
    if citations:
        return verdict
    if REFUSAL_RE.search(answer):
        return SufficiencyStatus.INSUFFICIENT
    return SufficiencyStatus.PARTIAL


def _grounded_status(status: SufficiencyStatus, grounding: GroundingReport) -> SufficiencyStatus:
    """Uncited data sentences cap an otherwise SUFFICIENT answer at PARTIAL."""
    if status is SufficiencyStatus.SUFFICIENT and not grounding.ok:
        logger.info("synthesis_unsupported_claims count=%d", len(grounding.unsupported))
        return SufficiencyStatus.PARTIAL
    return status


_INTERRUPTED_NOTE = (
    "\n\n⚠️ Câu trả lời bị gián đoạn do mất kết nối với mô hình AI, "
    "nội dung phía trên có thể chưa đầy đủ."
)
