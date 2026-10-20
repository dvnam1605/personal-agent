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

import asyncio
import contextlib
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.http import llm_http_timeout
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
from app.services.retrieval.prompts import (
    SYNTHESIS_EXTERNAL_SYSTEM_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
    build_synthesis_user_message,
)

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
# Citation extraction helpers
# ---------------------------------------------------------------------------

_CITE_RE = re.compile(r"\[(?:evidence_id=)?[\"']?([0-9a-fA-F]{32})[\"']?\]")
_HEX_ID_RE = re.compile(r"[0-9a-fA-F]{32}")
_BRACKET_CITE_RE = re.compile(r"\[(?:\s*(?:evidence_id=)?[\"']?[0-9a-fA-F]{32}[\"']?\s*[,;\s]*)+\]")


def _vn_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else text


def format_document_title(
    raw_title: str | None,
    text_content: str | None = None,
    anchors: dict[str, Any] | None = None,
) -> str:
    """Human title for a citation, for any document kind.

    Prefers the document profile (subtype, number, date, title); falls back
    to a printed "Số: …" line, then to a cleaned-up filename.
    """
    anchors = anchors or {}
    profile_title = (anchors.get("profile_title") or "").strip()
    number = anchors.get("doc_number")
    subtype = anchors.get("doc_subtype")
    when = _vn_date(anchors.get("doc_date"))
    if profile_title or number:
        label = profile_title[:120] if profile_title else ""
        if number and number not in label:
            head = f"{subtype or 'Văn bản'} số {number}"
            label = f"{head} – {label}" if label else head
        if when:
            label = f"{label} ({when})"
        return label

    if not raw_title:
        return "Tài liệu nội bộ"

    if text_content:
        so_match = re.search(r"Số\s*:\s*(\d{1,5}\s*/\s*[A-ZĐ][\wĐ\-/]*)", text_content)
        if so_match:
            number = re.sub(r"\s+", "", so_match.group(1))
            return f"Văn bản số {number}"

    cleaned = re.sub(r"\.(?:md|pdf|docx?|xlsx?)$", "", raw_title, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\d+-\d+-\d+-\d+_", "", cleaned)
    return cleaned.replace("_", " ").strip() or raw_title


def extract_cited_ids(text: str) -> list[str]:
    """Pull unique evidence_id hex strings from single or multi-evidence bracket markers."""
    ids: list[str] = []
    for bracket_match in _BRACKET_CITE_RE.finditer(text):
        for eid in _HEX_ID_RE.findall(bracket_match.group(0)):
            if eid not in ids:
                ids.append(eid)
    return ids


def build_citations_from_bundle(
    cited_ids: list[str],
    bundle: EvidenceBundle,
) -> list[Citation]:
    """Map cited evidence ids back to ``Citation`` objects using bundle items."""
    id_to_item = {item.evidence_id: item for item in bundle.items}
    citations: list[Citation] = []
    seen: set[str] = set()
    for eid in cited_ids:
        item = id_to_item.get(eid)
        if item is None or item.evidence_id in seen:
            if item is None:
                logger.debug("cited_evidence_not_in_bundle", extra={"evidence_id": eid})
            continue
        seen.add(item.evidence_id)
        raw_title = (
            item.title or item.anchors.get("document_title") or item.filename or "Văn bản nội bộ"
        )
        title = format_document_title(raw_title, item.content_raw, item.anchors)
        source_type = item.source_type or item.anchors.get("source_type") or "Văn bản"
        page_start = item.anchors.get("page_start")
        page_end = item.anchors.get("page_end")
        section_title = (
            " > ".join(item.heading_path)
            if item.heading_path
            else item.anchors.get("section_title")
        )
        citations.append(
            Citation(
                evidence_id=item.evidence_id,
                document_id=item.document_id,
                title=title,
                page_start=page_start,
                page_end=page_end,
                heading_path=list(item.heading_path),
                source_type=source_type,
                chunk_id=item.primary_chunk_id or item.evidence_id,
                text=item.content_raw,
                page_number=page_start or 1,
                section_title=section_title,
                score=item.score or item.rerank_score,
                source_uri=item.anchors.get("source_uri") or item.anchors.get("uri"),
                document_version_id=item.document_version_id,
                version_number=item.anchors.get("version_number"),
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
        stream_generate: StreamGenerateCallback | None = None,
    ) -> None:
        if generate is not None:
            self._generate = generate
        else:
            self._generate = self._build_default_generate() or _unconfigured_generate

        if stream_generate is not None:
            self._stream_generate = stream_generate
        else:
            self._stream_generate = (
                self._build_default_stream_generate() or _unconfigured_stream_generate
            )

    @staticmethod
    def _build_default_generate() -> GenerateCallback | None:
        try:
            api_key = settings.llm.openai_api_key
            if not api_key:
                return None

            async def _openai_generate(system: str, user: str) -> str:
                target_url = settings.llm.chat_completions_url()
                logger.info(
                    "synthesis_calling_llm url=%s model=%s mode=%s",
                    target_url,
                    settings.llm.primary_model,
                    str(settings.llm.mode),
                )
                timeout = settings.timeouts.llm_request_seconds
                chunks: list[str] = []
                stream_status: int | None = None
                # Use streaming under the hood so intermediate chunks keep proxy connection alive
                # and prevent 20s empty-body timeouts from upstream reverse proxies.
                # httpx timeouts are per read, so a slow-trickling stream also
                # gets an overall deadline.
                try:
                    async with (
                        asyncio.timeout(timeout),
                        httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client,
                    ):
                        async with client.stream(
                            "POST",
                            target_url,
                            headers={"Authorization": f"Bearer {api_key}"},
                            json={
                                "model": settings.llm.primary_model,
                                "messages": [
                                    {"role": "system", "content": system},
                                    {"role": "user", "content": user},
                                ],
                                "temperature": settings.llm.temperature,
                                "stream": True,
                            },
                        ) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                line = line.strip()
                                if not line or not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    payload = json.loads(data_str)
                                    choices = payload.get("choices") or []
                                    if choices:
                                        delta = choices[0].get("delta", {}).get("content")
                                        if delta:
                                            chunks.append(delta)
                                except Exception:  # noqa: BLE001 - malformed chunk should not abort stream
                                    continue
                except Exception as stream_err:  # noqa: BLE001 - stream failure falls back to standard POST
                    logger.warning("synthesis_stream_accumulation_failed error=%s", stream_err)
                    if isinstance(stream_err, httpx.HTTPStatusError):
                        stream_status = stream_err.response.status_code
                    partial = "".join(chunks).strip()
                    if partial:
                        # Never pass a cut-off answer off as a complete one.
                        raise SynthesisInterruptedError(partial) from stream_err
                    if isinstance(stream_err, (httpx.ConnectError, httpx.ConnectTimeout)):
                        # Gateway unreachable: a second POST would just wait again.
                        raise ValueError("LLM gateway unreachable") from stream_err

                content = "".join(chunks).strip()
                if not content and stream_status == 429:
                    # Rate-limited gateway: an immediate second POST only burns quota.
                    raise ValueError("LLM gateway rate-limited the synthesis request (429)")
                if not content:
                    # Fallback to standard POST if streaming produced no chunks
                    async with httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client:
                        resp = await client.post(
                            target_url,
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
                        if resp.content:
                            data = resp.json()
                            content = data["choices"][0]["message"].get("content") or ""

                if not content or not str(content).strip():
                    raise ValueError(
                        f"LLM returned empty content (model={settings.llm.primary_model})"
                    )
                return str(content)

            return _openai_generate
        except (ImportError, OSError, TimeoutError, TypeError, ValueError, KeyError):
            logger.exception("synthesis_default_generate_unavailable")
            return None

    @staticmethod
    def _build_default_stream_generate() -> StreamGenerateCallback | None:
        try:
            api_key = settings.llm.openai_api_key
            if not api_key:
                return None

            async def _openai_stream_generate(system: str, user: str) -> AsyncGenerator[str, None]:
                target_url = settings.llm.chat_completions_url()
                logger.info(
                    "synthesis_calling_llm_stream url=%s model=%s mode=%s",
                    target_url,
                    settings.llm.primary_model,
                    str(settings.llm.mode),
                )
                timeout = settings.timeouts.llm_request_seconds
                async with httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client:
                    async with client.stream(
                        "POST",
                        target_url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": settings.llm.primary_model,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            "temperature": settings.llm.temperature,
                            "stream": True,
                        },
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line or not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                payload = json.loads(data_str)
                                choices = payload.get("choices") or []
                                if choices:
                                    delta = choices[0].get("delta", {}).get("content")
                                    if delta:
                                        yield delta
                            except Exception:  # noqa: BLE001 - malformed chunk should not abort stream
                                continue

            return _openai_stream_generate
        except (ImportError, OSError, TimeoutError, TypeError, ValueError, KeyError):
            logger.exception("synthesis_default_stream_generate_unavailable")
            return None

    async def synthesize(
        self,
        question: str,
        bundle: EvidenceBundle,
        *,
        internal_only: bool = True,
        missing_documents: list[str] | None = None,
        verdict_status: SufficiencyStatus = SufficiencyStatus.SUFFICIENT,
        conversation_history: list[dict[str, str]] | None = None,
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

        system_msg = SYNTHESIS_SYSTEM_PROMPT if internal_only else SYNTHESIS_EXTERNAL_SYSTEM_PROMPT
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
        cleaned_answer = numberer.rewrite(answer_text).strip()
        # Only what the model actually cited — never a stand-in "top 3".
        citations = build_citations_from_bundle(numberer.cited_ids, bundle)
        status = _uncited_status(cleaned_answer, citations, verdict_status)
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
        grounding = check_answer(cleaned_answer)
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
                    if numberer.looks_like_citation(tag):
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
        grounding = check_answer("".join(streamed))
        if failure is None:
            status = _uncited_status("".join(streamed), citations, status)
            if not citations and status is SufficiencyStatus.PARTIAL:
                yield {"type": "token", "delta": _UNCITED_NOTE}
            status = _grounded_status(status, grounding)

        yield {
            "type": "citations",
            "citations": [c.model_dump(mode="json") for c in citations],
            "status": status.value,
            "unsupported_claims": grounding.unsupported,
            "retrieval_trace_id": bundle.retrieval_trace_id,
            "evidence": evidence_snapshot(bundle, numberer),
        }


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


class SynthesisInterruptedError(Exception):
    """The LLM stream broke after producing part of the answer."""

    def __init__(self, partial: str) -> None:
        super().__init__("LLM stream interrupted after partial output")
        self.partial = partial


GenerateCallback = Callable[[str, str], Awaitable[str]]
StreamGenerateCallback = Callable[[str, str], AsyncGenerator[str, None]]


async def _unconfigured_generate(system: str, user: str) -> str:
    """Fallback when no LLM key or generate callback is configured."""
    logger.warning(
        "synthesis_unconfigured_generate_invoked",
        extra={"hint": "inject a real LLM generate callback in production"},
    )
    return (
        "Answer synthesis is not yet configured.  "
        "Please provide a generate callback to PromptAnswerSynthesizer."
    )


async def _unconfigured_stream_generate(system: str, user: str) -> AsyncGenerator[str, None]:
    """Fallback generator when no LLM key or stream generate callback is configured."""
    logger.warning(
        "synthesis_unconfigured_stream_generate_invoked",
        extra={"hint": "inject a real LLM stream generate callback in production"},
    )
    yield "Answer synthesis is not yet configured. Please provide a generate callback to PromptAnswerSynthesizer."
