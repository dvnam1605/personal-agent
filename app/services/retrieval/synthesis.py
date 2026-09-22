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

import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.domain.models.retrieval import EvidenceBundle
from app.domain.models.retrieval.citation import Citation
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
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
_BRACKET_CITE_RE = re.compile(
    r"\[(?:\s*(?:evidence_id=)?[\"']?[0-9a-fA-F]{32}[\"']?\s*[,;\s]*)+\]"
)


def format_document_title(raw_title: str | None, text_content: str | None = None) -> str:
    """Format raw storage filenames (e.g. 18-3-2026-954776_427QD_25_02_2026.md) into clean administrative titles."""
    if not raw_title:
        return "Văn bản nội bộ"

    if text_content:
        so_match = re.search(
            r"Số:\s*([0-9A-Za-z\/\-\_]+QĐ[0-9A-Za-z\/\-\_]*)", text_content, re.IGNORECASE
        )
        ve_viec = re.search(r"QUYẾT ĐỊNH\s+(?:Về việc\s+)?([^\n\r#]+)", text_content, re.IGNORECASE)
        if so_match and ve_viec:
            clean_so = so_match.group(1).strip()
            clean_subject = ve_viec.group(1).strip().capitalize()
            return f"QĐ số {clean_so} - {clean_subject[:80]}"
        if so_match:
            return f"Quyết định số {so_match.group(1).strip()}"

    fn_match = re.search(r"_(\d+)QD_(\d{1,2})_(\d{1,2})_(\d{4})", raw_title, re.IGNORECASE)
    if fn_match:
        so, day, month, year = fn_match.groups()
        return f"Quyết định số {so}/QĐ-TNVN ({day.zfill(2)}/{month.zfill(2)}/{year})"

    ct_match = re.search(r"_CT(\d+)_(\d{1,2})_(\d{1,2})_(\d{4})", raw_title, re.IGNORECASE)
    if ct_match:
        so, day, month, year = ct_match.groups()
        return f"Chỉ thị số {so}/CT-TNVN ({day.zfill(2)}/{month.zfill(2)}/{year})"

    cleaned = re.sub(r"\.md$", "", raw_title, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\d+-\d+-\d+-\d+_", "", cleaned)
    return cleaned or raw_title


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
        title = format_document_title(raw_title, item.content_raw)
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
                source_uri=item.anchors.get("source_uri"),
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
                # Use streaming under the hood so intermediate chunks keep proxy connection alive
                # and prevent 20s empty-body timeouts from upstream reverse proxies
                try:
                    async with httpx.AsyncClient(timeout=timeout) as client:
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

                content = "".join(chunks).strip()
                if not content:
                    # Fallback to standard POST if streaming produced no chunks
                    async with httpx.AsyncClient(timeout=timeout) as client:
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
                async with httpx.AsyncClient(timeout=timeout) as client:
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

        try:
            answer_text = await self._generate(system_msg, user_msg)
        except Exception as gen_err:  # noqa: BLE001 - fallback to raw evidence on LLM failure
            logger.error("synthesis_generate_failed error=%s", gen_err)
            fallback_eids = [it.evidence_id for it in bundle.items[:3]]
            citations = build_citations_from_bundle(fallback_eids, bundle)
            return SynthesisResult(
                answer=(
                    f"⚠️ Không thể kết nối đến mô hình AI để tạo tóm tắt chi tiết ({gen_err}). "
                    "Dưới đây là các tài liệu liên quan được trích xuất từ kho tri thức:"
                ),
                citations=citations,
                status=SufficiencyStatus.SUFFICIENT
                if citations
                else SufficiencyStatus.INSUFFICIENT,
            )

        cited_ids = extract_cited_ids(answer_text)
        citations = build_citations_from_bundle(cited_ids, bundle)

        # If LLM didn't cite any explicit ID but evidence was retrieved, link top bundle items
        if not citations and bundle.items:
            fallback_eids = [it.evidence_id for it in bundle.items[:3]]
            citations = build_citations_from_bundle(fallback_eids, bundle)

        # Map each cited evidence_id to a 1-based footnote [1], [2], etc.
        eid_to_index = {cite.evidence_id: str(i + 1) for i, cite in enumerate(citations)}

        def _clean_cite(match: re.Match[str]) -> str:
            raw_bracket = match.group(0)
            eids = _HEX_ID_RE.findall(raw_bracket)
            badges: list[str] = []
            for eid in eids:
                idx = eid_to_index.get(eid)
                if idx and f"[{idx}]" not in badges:
                    badges.append(f"[{idx}]")
            return "".join(badges)

        cleaned_answer = _BRACKET_CITE_RE.sub(_clean_cite, answer_text)
        # Clean any remaining orphan hex evidence IDs or unclosed brackets
        cleaned_answer = re.sub(r"\[\s*[0-9a-fA-F]{32}[^\]\n]*\]?", "", cleaned_answer)
        cleaned_answer = re.sub(r"\b[0-9a-fA-F]{32}\b", "", cleaned_answer)
        cleaned_answer = re.sub(r"\[\s*\]", "", cleaned_answer)
        cleaned_answer = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned_answer)

        return SynthesisResult(
            answer=cleaned_answer.strip(),
            citations=citations,
            status=verdict_status,
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

        eid_to_index: dict[str, int] = {}
        cited_ids: list[str] = []
        buf = ""

        def _process_buffer(force: bool = False) -> str:
            nonlocal buf
            out = ""
            while buf:
                bracket_pos = buf.find("[")
                if bracket_pos == -1:
                    out += re.sub(r"\b[0-9a-fA-F]{32}\b", "", buf)
                    buf = ""
                    break
                if bracket_pos > 0:
                    out += re.sub(r"\b[0-9a-fA-F]{32}\b", "", buf[:bracket_pos])
                    buf = buf[bracket_pos:]
                    continue

                # buf starts with '['
                close_pos = buf.find("]")
                if close_pos != -1:
                    tag = buf[: close_pos + 1]
                    if _BRACKET_CITE_RE.fullmatch(tag):
                        eids = _HEX_ID_RE.findall(tag)
                        tag_out = ""
                        for eid in eids:
                            if eid not in eid_to_index:
                                idx = len(eid_to_index) + 1
                                eid_to_index[eid] = idx
                                cited_ids.append(eid)
                            else:
                                idx = eid_to_index[eid]
                            if f"[{idx}]" not in tag_out:
                                tag_out += f"[{idx}]"
                        out += tag_out
                    elif _HEX_ID_RE.search(tag):
                        # Contains hex IDs but malformed tag -> drop raw hashes
                        out += ""
                    else:
                        out += tag
                    buf = buf[close_pos + 1 :]
                    continue

                # No closing ']' yet
                if force:
                    if _HEX_ID_RE.search(buf):
                        buf = ""
                    else:
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

        try:
            async for chunk in self._stream_generate(system_msg, user_msg):
                buf += chunk
                emitted = _process_buffer(force=False)
                if emitted:
                    yield {"type": "token", "delta": emitted}
        except Exception as stream_err:  # noqa: BLE001 - fallback gracefully on LLM stream failure
            logger.error("synthesis_stream_generate_failed error=%s", stream_err)
            yield {
                "type": "token",
                "delta": (
                    f"\n\n⚠️ Lỗi kết nối mô hình AI ({stream_err}). "
                    "Dưới đây là các tài liệu liên quan được trích xuất từ kho tri thức:\n"
                ),
            }

        # Flush any remaining buffer
        final_emitted = _process_buffer(force=True)
        if final_emitted:
            yield {"type": "token", "delta": final_emitted}

        # Build citations
        citations = build_citations_from_bundle(cited_ids, bundle)
        if not citations and bundle.items:
            fallback_eids = [it.evidence_id for it in bundle.items[:3]]
            citations = build_citations_from_bundle(fallback_eids, bundle)

        yield {
            "type": "citations",
            "citations": [c.model_dump(mode="json") for c in citations],
            "status": verdict_status.value,
        }


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
