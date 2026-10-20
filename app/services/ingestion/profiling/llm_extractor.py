"""Single-call LLM fallback for profiling documents the rules are unsure about.

One non-streaming request with a strict JSON contract; any failure (gateway
429, timeout, malformed reply) returns ``None`` so ingestion keeps the rule
result and flags the profile for review instead of failing the document.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.http import llm_http_timeout
from app.core.jsonutil import extract_json_object
from app.services.ingestion.profiling.categories import DocCategory
from app.services.ingestion.profiling.signals import DocumentSignals

logger = logging.getLogger(__name__)

HEADER_CHARS = 2000
TAIL_CHARS = 1000
MAX_HEADINGS = 30

_SYSTEM = (
    "Bạn trích xuất metadata từ tài liệu nội bộ (mọi loại: văn bản hành chính, báo cáo, "
    "hợp đồng, biên bản, tài liệu kỹ thuật, đồ án/luận văn, bảng tính). "
    "Chỉ dùng thông tin có trong văn bản; trường nào không có thì để null. "
    "Chỉ trả về một đối tượng JSON, không giải thích."
)

_SCHEMA_HINT = {
    "doc_category": "|".join(c.value for c in DocCategory),
    "doc_subtype": "string|null (ví dụ: Tờ trình, Biên bản họp, Đồ án tốt nghiệp)",
    "title": "string|null",
    "doc_number": "string|null",
    "doc_date": "YYYY-MM-DD|null",
    "issuer": "string|null (cơ quan/đơn vị/tổ chức phát hành)",
    "subject": "string|null (trích yếu một câu)",
    "people": [
        {
            "name": "string",
            "role": "string",
            "kind": "author|signer|supervisor|party|attendee|chair",
        }
    ],
    "extras": "object (trường riêng theo loại: period, parties, value, attendees, supervisor, isbn...)",
}


def _prompt(signals: DocumentSignals) -> str:
    header = signals.header_text[:HEADER_CHARS]
    tail = signals.tail_text[:TAIL_CHARS]
    headings = "\n".join(signals.headings[:MAX_HEADINGS])
    return (
        f"Trả về JSON theo khuôn: {json.dumps(_SCHEMA_HINT, ensure_ascii=False)}\n\n"
        f"TÊN FILE: {signals.filename}\n\n"
        f"--- PHẦN ĐẦU ---\n{header}\n\n"
        f"--- CÁC ĐỀ MỤC ---\n{headings}\n\n"
        f"--- PHẦN CUỐI ---\n{tail}\n"
    )


async def extract_profile_with_llm(signals: DocumentSignals) -> dict[str, Any] | None:
    """Return the parsed JSON object, or ``None`` on any failure."""
    api_key = str(settings.llm.openai_api_key or "")
    if not api_key:
        return None
    timeout = min(settings.timeouts.llm_request_seconds, settings.profiling.llm_timeout_seconds)
    payload = {
        "model": settings.llm.primary_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _prompt(signals)},
        ],
        "temperature": 0.0,
    }
    try:
        async with (
            asyncio.timeout(timeout),
            httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client,
        ):
            resp = await client.post(
                settings.llm.chat_completions_url(),
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content") or ""
        return extract_json_object(content)
    except Exception as exc:  # noqa: BLE001 - profiling must never fail ingestion
        logger.warning("profile_llm_extraction_failed file=%s error=%s", signals.filename, exc)
        return None
