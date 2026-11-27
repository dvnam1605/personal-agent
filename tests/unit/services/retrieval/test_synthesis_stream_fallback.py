"""Streaming synthesis falls back to the top evidence when the LLM never answers."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

from app.domain.models.retrieval import Evidence, EvidenceBundle
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.services.retrieval.answer.synthesis import PromptAnswerSynthesizer


def _evidence(content: str) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4().hex,
        kind="CHILD",
        content_raw=content,
        token_estimate=10,
        primary_chunk_id=f"chunk-{uuid.uuid4().hex[:8]}",
        document_id="doc-1",
    )


def _bundle(items: list[Evidence]) -> EvidenceBundle:
    return EvidenceBundle(
        items=items,
        total_tokens=sum(e.token_estimate for e in items),
        documents_used=sorted({e.document_id for e in items}),
        parent_ids_used=[],
        retrieval_trace_id=uuid.uuid4().hex,
    )


async def _collect(synth: PromptAnswerSynthesizer, bundle: EvidenceBundle) -> dict[str, Any]:
    final: dict[str, Any] = {}
    async for event in synth.synthesize_stream("Hỏi?", bundle):
        if event["type"] == "citations":
            final = event
    return final


async def test_stream_failure_before_tokens_cites_top_evidence() -> None:
    async def broken(system: str, user: str) -> AsyncGenerator[str, None]:
        raise RuntimeError("upstream down")
        yield ""  # pragma: no cover - makes this an async generator

    items = [_evidence(f"nội dung {i}") for i in range(5)]
    bundle = _bundle(items)
    final = await _collect(PromptAnswerSynthesizer(stream_generate=broken), bundle)

    assert [c["evidence_id"] for c in final["citations"]] == [e.evidence_id for e in items[:3]]
    assert final["status"] == SufficiencyStatus.PARTIAL.value


async def test_stream_failure_after_tokens_keeps_only_cited_ids() -> None:
    async def flaky(system: str, user: str) -> AsyncGenerator[str, None]:
        yield "Một phần không dẫn nguồn "
        raise RuntimeError("reset")

    final = await _collect(
        PromptAnswerSynthesizer(stream_generate=flaky), _bundle([_evidence("a"), _evidence("b")])
    )
    assert final["citations"] == []
